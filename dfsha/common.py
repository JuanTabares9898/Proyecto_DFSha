"""TLS, tokens firmados y acceso transaccional a un clúster etcd real."""
import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
import ssl
import time
import urllib.error
import urllib.request


class Error(Exception):
    def __init__(self, status, message):
        self.status, self.message = status, message
        super().__init__(f'{status}: {message}')


def b64(value):
    if isinstance(value, str):
        value = value.encode()
    return base64.b64encode(value).decode()


def unb64(value):
    return base64.b64decode(value)


def packed(value):
    return json.dumps(value, separators=(',', ':'), sort_keys=True).encode()


def digest(value):
    return hashlib.sha256(value).hexdigest()


def token(secret, payload, audience, ttl=300):
    header = b64(packed({'alg': 'HS256', 'typ': 'JWT'})).rstrip('=')
    payload = {**payload, 'aud': audience, 'exp': int(time.time())+ttl,
               'iss': 'dfsha-aj', 'iat': int(time.time())}
    data = header+'.'+b64(packed(payload)).rstrip('=')
    sig = hmac.new(secret.encode(), data.encode(), hashlib.sha256).digest()
    return data+'.'+b64(sig).rstrip('=')


def verify(secret, encoded, audience):
    try:
        header, body, signature = encoded.split('.')
        decoded_header = json.loads(unb64(header+'='*(-len(header)%4)))
        if decoded_header != {'alg': 'HS256', 'typ': 'JWT'}:
            raise ValueError('algorithm')
        expected = hmac.new(secret.encode(), (header+'.'+body).encode(), hashlib.sha256).digest()
        actual = unb64(signature+'='*(-len(signature)%4))
        claims = json.loads(unb64(body+'='*(-len(body)%4)))
        if not hmac.compare_digest(expected, actual):
            raise ValueError('signature')
        if claims['aud'] != audience or claims['iss'] != 'dfsha-aj' or claims['exp'] <= time.time():
            raise ValueError('expired or audience')
        return claims
    except (KeyError, ValueError, TypeError, json.JSONDecodeError):
        raise Error(401, 'token inválido o vencido') from None


def tls(config, identity=None):
    ctx = ssl.create_default_context(cafile=config['ca'])
    ctx.minimum_version = ssl.TLSVersion.TLSv1_3
    if identity:
        ctx.load_cert_chain(config[identity+'_cert'], config[identity+'_key'])
    return ctx


def request(url, method='GET', data=None, *, context=None, headers=None, timeout=6, raw=False):
    hdr = dict(headers or {})
    if data is not None and not isinstance(data, bytes):
        data = packed(data)
        hdr['Content-Type'] = 'application/json'
    req = urllib.request.Request(url, data=data, method=method, headers=hdr)
    try:
        with urllib.request.urlopen(req, context=context, timeout=timeout) as response:
            body = response.read()
            return body if raw else json.loads(body or '{}')
    except urllib.error.HTTPError as exc:
        try:
            message = json.loads(exc.read()).get('error', str(exc))
        except (ValueError, TypeError):
            message = str(exc)
        raise Error(exc.code, message) from None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise Error(503, 'conexión no disponible: '+str(exc)) from None


class Etcd:
    def __init__(self, config):
        self.urls = config['etcd']
        self.ctx = tls(config, 'service')
        self.prefix = '/dfsha-aj/'

    def call(self, path, body):
        last = None
        for url in self.urls:
            try:
                answer = request(url+'/v3/'+path, 'POST', body, context=self.ctx, timeout=2)
                if 'error' in answer:
                    raise Error(503, str(answer['error']))
                return answer
            except Error as exc:
                last = exc
        raise Error(503, 'sin quórum de metadatos: '+str(last))

    def key(self, key):
        return b64(self.prefix+key)

    def get(self, key):
        result = self.call('kv/range', {'key': self.key(key), 'serializable': False})
        if not result.get('kvs'):
            return None, 0
        row = result['kvs'][0]
        return json.loads(unb64(row['value'])), int(row['mod_revision'])

    def scan(self, prefix):
        start = (self.prefix+prefix).encode()
        end = start[:-1]+bytes([start[-1]+1])
        result = self.call('kv/range', {'key': b64(start), 'range_end': b64(end), 'serializable': False})
        return [(unb64(r['key']).decode()[len(self.prefix):], json.loads(unb64(r['value'])),
                 int(r['mod_revision'])) for r in result.get('kvs', [])]

    def put_op(self, key, value, lease=None):
        args = {'key': self.key(key), 'value': b64(packed(value))}
        if lease:
            args['lease'] = str(lease)
        return {'request_put': args}

    def delete_op(self, key):
        return {'request_delete_range': {'key': self.key(key)}}

    def compare(self, key, revision):
        return {'key': self.key(key), 'target': 'MOD', 'result': 'EQUAL', 'mod_revision': str(revision)}

    def txn(self, compares, operations):
        result=self.call('kv/txn', {'compare': compares, 'success': operations, 'failure': []})
        # Protobuf JSON puede omitir false; una comparación fallida es normal.
        result.setdefault('succeeded',False)
        return result

    def cas(self, key, revision, value, lease=None):
        return self.txn([self.compare(key, revision)], [self.put_op(key, value, lease)])

    def lease(self, ttl):
        answer = self.call('lease/grant', {'TTL': str(ttl)})
        return answer['ID']

    def revoke(self, lease):
        return self.call('lease/revoke', {'ID': str(lease)})


def load_config():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', default=os.environ.get('DFSHA_CONFIG'))
    args = parser.parse_args()
    if not args.config:
        parser.error('Indique --config o DFSHA_CONFIG')
    return json.loads(Path(args.config).read_text())

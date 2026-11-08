import logging
from .common import load_config
from .httpserver import serve


def main():
    logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(message)s')
    config=load_config()
    if config['role']=='control':
        from .control import Control
        app=Control(config)
    else:
        from .data import Data
        app=Data(config)
    serve(app,config)


if __name__=='__main__':main()

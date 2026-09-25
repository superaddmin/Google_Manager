import argparse
import json
import os
import sys
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

from cryptography.fernet import Fernet
from werkzeug.serving import make_server


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests.recharge_flow_server import SyntheticUpstream, build_upstream_handler, database_uri
from app import create_app, db
from app.config import TestingConfig
from app.models.cdk import CdkCard, CdkRedemption, CdkStock
from app.models.recharge_task import RechargeTask
from app.services.cdk_identity import create_staff


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--database', required=True)
    args = parser.parse_args()
    os.environ['NO_PROXY'] = '127.0.0.1,localhost'
    os.environ['no_proxy'] = '127.0.0.1,localhost'
    upstream = SyntheticUpstream()
    upstream_server = ThreadingHTTPServer(('127.0.0.1', 0), build_upstream_handler(upstream))
    threading.Thread(target=upstream_server.serve_forever, daemon=True).start()
    TestingConfig.SQLALCHEMY_DATABASE_URI = database_uri(Path(args.database))
    application = create_app('testing')
    application.config.update(CDK_ENABLED=True, CDK_ENCRYPTION_KEYS={'v1': Fernet.generate_key().decode()},
                              CDK_LOOKUP_KEYS={'v1': Fernet.generate_key().decode()},
                              GMAIL_TOKEN_ENCRYPTION_KEY=Fernet.generate_key().decode(),
                              RECHARGE_MODE='live', RECHARGE_UPSTREAM_URL=f'http://127.0.0.1:{upstream_server.server_port}')
    with application.app_context():
        create_staff('maker', 'maker-browser-fixture-password', 'admin', ['*'])
        create_staff('checker', 'checker-browser-fixture-password', 'admin', ['*'])
        db.session.commit()

    @application.get('/__test__/state')
    def state():
        return {'upstream': upstream.snapshot(), 'tasks': RechargeTask.query.count(),
                'redemptions': CdkRedemption.query.count(), 'cards': [record.usage for record in CdkCard.query.all()],
                'stocks': [record.state for record in CdkStock.query.all()]}

    server = make_server('127.0.0.1', 0, application, threaded=True)

    @application.post('/__test__/shutdown')
    def shutdown():
        threading.Thread(target=server.shutdown, daemon=True).start()
        return {'stopping': True}

    print(json.dumps({'base_url': f'http://127.0.0.1:{server.server_port}'}), flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()
        upstream_server.shutdown()
        upstream_server.server_close()
        with application.app_context():
            db.session.remove()
            db.engine.dispose()


if __name__ == '__main__':
    main()

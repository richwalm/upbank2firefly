#!/usr/bin/env python3
from flask import Flask, request, abort
import os
import hmac
import urllib.request
import json

app = Flask(__name__)

if 'UPBANK_PAT' not in os.environ:
    raise Exception('You need to define UPBANK_PAT.')
if b'UPBANK_SECRET' not in os.environb:
    raise Exception('You need to define UPBANK_SECRET.')
if 'FIREFLY_PAT' not in os.environ:
    raise Exception('You need to define FIREFLY_PAT.')
if 'FIREFLY_BASEURL' not in os.environ:
    raise Exception('You need to define FIREFLY_BASEURL.')
Timeout = 10

def DeleteTransaction(ID):
    app.logger.info('Received a delete message for ID; {}'.format(ID))
    # TODO: Search for the transaction.
    # TODO: Delete it.
    pass

def HandleTransaction(Type, TransactionData):
    app.logger.info('Received a {} message to process.'.format(Type))
    # TODO: Search for the transaction.
    # TODO: Update in Firefly.
    pass

@app.route('/', methods = ['POST'])
def index():
    # First off, check to ensure that this is from Up Bank.
    AuthHeader = request.headers.get('X-Up-Authenticity-Signature')
    if not AuthHeader:
        app.logger.warn('Missing X-Up-Authenticity-Signature header.')
        abort(403)

    Body = request.data
    if not Body:
        app.logger.warn('Missing body.')
        abort(403)

    HMAC = hmac.new(os.environb[b'UPBANK_SECRET'], Body, 'sha256')
    Digest = HMAC.hexdigest()
    if not hmac.compare_digest(Digest, AuthHeader):
        app.logger.error('HMAC did\'t match; %s != %s', Digest, AuthHeader)
        abort(403)

    # Valid message. Convert to JSON.
    JSON = request.get_json(cache = False)

    # Get and validate message types.
    try:
        if JSON['data']['type'] != 'webhook-events':
            raise Exception('Unexpected resource type; {}'.format(JSON['data']['type']))
    except Exception:
        app.logger.exception('Exception when detecting resource type.')
        abort(400)

    try:
        Type = JSON['data']['attributes']['eventType']
    except Exception:
        app.logger.exception('Failed to obtain resource event type.')
        abort(400)

    # Handle types.
    if Type == 'PING':
        app.logger.info('Received a ping message.')
        return 'PONG'

    elif Type in {'TRANSACTION_CREATED', 'TRANSACTION_CREATED'}:
        try:
            TransactionURL = JSON['data']['relationships']['transaction']['links']['related']
        except Exception:
            app.logger.exception('{} payload missing transaction URL.'.format(Type))
            abort(400)

        # Download URL & convert to JSON.
        try:
            Req = urllib.request.Request(TransactionURL)
            Req.add_header('Authorization', 'Bearer {}'.format(os.environ['UPBANK_PAT']))
            Resp = urllib.request.urlopen(Req, timeout = Timeout)
            JSONResp = json.load(Resp)

        except urllib.error.HTTPError as e:
            app.logger.exception('HTTP error while downloading transaction.')
            if e.code == 404:
                abort(400)
            abort(500)
        except json.JSONDecodeError:
            app.logger.exception('Transaction JSON is not valid.')
            abort(400)
        except Exception:
            app.logger.exception('Failed to download transaction.')
            abort(500)

        HandleTransaction(Type, JSONResp)

    elif Type == 'TRANSACTION_DELETED':
        try:
            TransactionID = JSON['data']['relationships']['transaction']['data']['id']
        except Exception:
            app.logger.exception('Delete payload missing transaction ID.')
            abort(400)

        DeleteTransaction(TransactionID)

    else:
        app.logger.error('Unexpected resource event type; {}'.format(Type))
        abort(400)

    return 'THANKS'

if __name__ == '__main__':
    app.run(host = '0.0.0.0', debug = True, port = 80)

#!/usr/bin/env python3
from flask import Flask, request, abort
import logging
import os
import hmac
import urllib.request
import json

app = Flask(__name__)
app.logger.setLevel(logging.INFO)

if 'UPBANK_PAT' not in os.environ:
    raise Exception('You need to define UPBANK_PAT.')
if b'UPBANK_SECRET' not in os.environb:
    raise Exception('You need to define UPBANK_SECRET.')
if 'FIREFLY_PAT' not in os.environ:
    raise Exception('You need to define FIREFLY_PAT.')
if 'FIREFLY_BASEURL' not in os.environ:
    raise Exception('You need to define FIREFLY_BASEURL.')

Timeout = 10

def PerformRequest(URL, PAT, Accept = None, Method = None, IsJSON = False):
    try:
        Req = urllib.request.Request(URL, method = Method)
        Req.add_header('Authorization', 'Bearer {}'.format(PAT))
        if Accept:
            Req.add_header('Accept', Accept)
        Resp = urllib.request.urlopen(Req, timeout = Timeout)
        if IsJSON:
            Data = json.load(Resp)
        else:
            Data = Resp.read()
    except urllib.error.HTTPError as e:
        app.logger.exception('Got HTTP status code %s while downloading; %s', e.code, URL)
        return None, True
    except json.JSONDecodeError:
        app.logger.exception('Expected JSON is not valid; %s', URL)
        return None, True
    except Exception:
        app.logger.exception('Failed to download JSON; %s', URL)
        return None, False

    return Data, None

def DeleteTransaction(ID):
    app.logger.info('Received a delete message for ID; %s', ID)

    # Search for the Up ID in Firefly.
    URL = '{}/api/v1/search/transactions?query=external_id:{}'.format(os.environ['FIREFLY_BASEURL'], ID)
    Data = PerformRequest(URL, os.environ['FIREFLY_PAT'], 'application/vnd.api+json', IsJSON = True)
    if not Data[0]:
        return False

    JSON = Data[0]

    try:
        Count = len(JSON['data'])
    except Exception:
        app.logger.error('Unexpected JSON from Firefly\'s URL; %s', URL)
        return False

    if not Count:
        app.logger.warning('No Firefly transaction with the external_id of %s.', ID)
        return False
    elif Count > 1:
        # Should be in most recent order so this shouldn't matter.
        app.logger.warning('Multiple transactions with the external_id of %s. Using the first.', ID)

    try:
        FireflyID = JSON['data'][0]['id']
    except Exception:
        app.logger.exception('Failed to extract ID from Firefly\'s search results for external_id; %s', ID)
        return False

    # Delete from Firefly.
    URL = '{}/api/v1/transaction/{}'.format(os.environ['FIREFLY_BASEURL'], FireflyID)
    Data = PerformRequest(URL, os.environ['FIREFLY_PAT'], None, 'DELETE')
    if not Data[0]:
        return False

    app.logger.info('Successfully deleted transaction %s (Up ID %s)', FireflyID, ID)
    return True

def HandleTransaction(Type, TransactionData):
    app.logger.info('Received a %s message to process.', Type)

    return False

def CheckMessageSecure():
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

@app.route('/', methods = ['POST'])
def index():
    CheckMessageSecure()

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

    elif Type in {'TRANSACTION_CREATED', 'TRANSACTION_SETTLED'}:
        try:
            TransactionURL = JSON['data']['relationships']['transaction']['links']['related']
        except Exception:
            app.logger.exception('%s payload missing transaction URL.', Type)
            abort(400)

        Data = PerformRequest(TransactionURL, os.environ['UPBANK_PAT'], IsJSON = True)
        if not Data[0]:
            abort(400 if Data[1] else 500)

        # Since we've gotten initial valid data from Up, return success from this point forward.
        try:
            HandleTransaction(Type, Data[0])
        except Exception:
            app.logger.exception('Failed while processing %s transaction.', Type)

    elif Type == 'TRANSACTION_DELETED':
        try:
            TransactionID = JSON['data']['relationships']['transaction']['data']['id']
        except Exception:
            app.logger.exception('Delete payload missing transaction ID.')
            abort(400)

        # Since we've gotten initial valid data from Up, return success from this point forward.
        try:
            DeleteTransaction(TransactionID)
        except Exception:
            app.logger.exception('Failed while processing delete transaction for %s.', TransactionID)

    else:
        app.logger.error('Unexpected resource event type; %s', Type)
        abort(400)

    return 'THANKS'

if __name__ == '__main__':
    app.run(host = '0.0.0.0', debug = True, port = 80)

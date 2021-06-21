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
if 'ACCOUNT_MAPPING' not in os.environ:
    raise Exception('You need to define ACCOUNT_MAPPING.')

Timeout = 10
Accounts = {}
Checking = None

CategoryIDs = {}

def SetupAccountMapping():
    global Checking

    String = os.environ['ACCOUNT_MAPPING']

    AccountStrings = String.split(',')
    if len(AccountStrings) < 2:
        raise Exception('ACCOUNT_MAPPING expects at least two accounts.')

    for Account in AccountStrings:
        Split = Account.find(':')
        if Split == -1:
            raise Exception('Missing sperator (:) in account mapping.')

        UpAccountID = Account[:Split]
        FireflyAccountID = Account[Split + 1:]

        try:
            FireflyAccountID = int(FireflyAccountID)
            if FireflyAccountID < 1:
                raise Exception('Firefly account ID is a negative interger.')
        except Exception:
            raise Exception('Firefly account ID is not valid.')

        Accounts[UpAccountID] = FireflyAccountID
        # We'll record the first as the checking account.
        if not Checking:
            Checking = UpAccountID

SetupAccountMapping()

def PerformRequest(URL, PAT, Accept = None, Method = None, IsJSON = False, Data = None):
    try:
        Req = urllib.request.Request(URL, data = Data, method = Method)
        Req.add_header('Authorization', 'Bearer {}'.format(PAT))
        if Accept:
            Req.add_header('Accept', Accept)
        if Data:
            Req.add_header('Content-Type', 'application/json')
        Resp = urllib.request.urlopen(Req, timeout = Timeout)
        if IsJSON:
            Reponse = json.load(Resp)
        else:
            Reponse = Resp.read()
    except urllib.error.HTTPError as e:
        app.logger.exception('Got HTTP status code %s while %s\'ing; %s', e.code, Method or 'GET', URL)
        print('Error;', e.read())
        return None, True
    except json.JSONDecodeError:
        app.logger.exception('Expected JSON is not valid; %s', URL)
        return None, True
    except Exception:
        app.logger.exception('Failed to download JSON; %s', URL)
        return None, False

    return Reponse, None

def ReadCategories(JSON):
    Categories = JSON['data']

    for Category in Categories:
        ID = Category['id']
        Name = Category['attributes']['name']

        CategoryIDs[ID] = Name

def GetCategoryName(ID):
    if ID in CategoryIDs:
        return CategoryIDs[ID]

    Resp = PerformRequest('https://api.up.com.au/api/v1/categories', os.environ['UPBANK_PAT'], IsJSON = True)
    if Resp[0]:
        ReadCategories(Resp[0])

    return CategoryIDs.get(ID, ID)   # Shouldn't occur but just return the ID. It's better than nothing.

def SearchFirefly(ID):
    # API Doc; https://api-docs.firefly-iii.org/#/search/searchTransactions
    URL = '{}/api/v1/search/transactions?query=external_id:{}'.format(os.environ['FIREFLY_BASEURL'], ID)
    Data = PerformRequest(URL, os.environ['FIREFLY_PAT'], 'application/vnd.api+json', IsJSON = True)
    if not Data[0]:
        return

    JSON = Data[0]

    try:
        Count = len(JSON['data'])
    except Exception:
        app.logger.error('Unexpected JSON from Firefly\'s URL; %s', URL)
        return

    if not Count:
        app.logger.warning('No Firefly transaction with the external_id of %s.', ID)
        return
    elif Count > 1:
        # Should be in most recent order so this shouldn't matter.
        app.logger.warning('Multiple transactions with the external_id of %s. Using the first.', ID)

    try:
        FireflyID = JSON['data'][0]['id']
    except Exception:
        app.logger.exception('Failed to extract ID from Firefly\'s search results for external_id; %s', ID)
        return

    return FireflyID

def DeleteTransaction(ID):
    app.logger.info('Received a delete message for ID; %s', ID)

    # Search for the Up ID in Firefly.
    FireflyID = SearchFirefly(ID)
    if not FireflyID:
        return False

    # API Doc; https://api-docs.firefly-iii.org/#/transactions/deleteTransaction
    URL = '{}/api/v1/transaction/{}'.format(os.environ['FIREFLY_BASEURL'], FireflyID)
    Data = PerformRequest(URL, os.environ['FIREFLY_PAT'], None, 'DELETE')
    if not Data[0]:
        return False

    app.logger.info('Successfully deleted transaction %s (Up ID %s)', FireflyID, ID)
    return True

def HandleAmount(Amount):
    IntPrice = Amount['valueInBaseUnits'] / 100
    StringPrice = float(Amount['value'])

    if IntPrice != StringPrice:
        raise Exception('Up amount values don\'t match. {} != {}'.format(IntPrice, StringPrice))

    return Amount['value'], Amount['currencyCode'], IntPrice

# Due to Firefly issue #3338, the time part of dates is removed on edits.
# Therefore, to keep transactions in the same order, we'll strip them off here.
def HandleDate(DateString):
    TimeStart = DateString.find('T')
    if TimeStart < 0:
        return DateString
    return DateString[:TimeStart]

def HandleTransaction(Type, Data):
    app.logger.info('Received a %s message to process.', Type)

    # API Doc; https://developer.up.com.au/#get_transactions_id

    try:
        ID = Data['data']['id']
    except Exception:
        app.logger.exception('Transaction is missing an ID.')
        return False

    # Create the transaction.
    # API Doc; https://api-docs.firefly-iii.org/#/transactions/storeTransaction
    Trans = {'transactions': [ {'external_id': ID} ]}
    FireflyBase = Trans['transactions'][0]
    UpBase = Data['data']

    # Update if we already have it.
    FireflyID = None
    if Type == 'TRANSACTION_SETTLED':
        FireflyID = SearchFirefly(ID)

    # Amounts.
    Amount = HandleAmount(UpBase['attributes']['amount'])
    ForeignAmount = None
    if UpBase['attributes']['foreignAmount']:
        ForeignAmount = HandleAmount(UpBase['attributes']['foreignAmount'])

    # Settled time.
    if UpBase['attributes']['status'] == 'SETTLED':
        FireflyBase['process_date'] = HandleDate(UpBase['attributes']['settledAt'])

    # New transaction.
    if not FireflyID:
        # Basic infomation.
        FireflyBase['date'] = FireflyBase['createdAt'] = HandleDate(UpBase['attributes']['createdAt'])
        Description = FireflyBase['description'] = UpBase['attributes']['description']

        # Category.
        Category = None
        if UpBase['relationships']['category']['data']:
            Category = GetCategoryName(UpBase['relationships']['category']['data']['id'])

        # Focus account.
        FocusAccount = UpBase['relationships']['account']['data']['id']
        if FocusAccount not in Accounts:
            raise Exception('Transaction {} has an unknown source account; {}'.format(ID, FocusAccount))
        FireflyAccountID = Accounts[FocusAccount]

        # Handle type.
        if UpBase['relationships']['transferAccount']['data']:
            # Transfer.
            # As we receive two transactions (incoming & outgoing) from Up, we'll disregard the incoming.
            if Amount[2] > 0:
                app.logger.info('Disregarding incoming transfer transaction; %s ($%s %s)', ID, Amount[2], Amount[1])
                return False
            DestAccount = UpBase['relationships']['transferAccount']['data']['id']
            if DestAccount not in Accounts:
                raise Exception('Transaction {} has an unknown destination account; {}'.format(ID, DestAccount))
            FireflyBase['source_id'] = FireflyAccountID
            FireflyBase['destination_id'] = Accounts[DestAccount]
            FireflyBase['type'] = 'transfer'
        else:
            # Bit of an API limitation here; https://github.com/up-banking/api/issues/80
            # Round Up and Save Transfers don't appear as transfers.
            # Withdrawal.
            if Amount[2] < 0:
                if Description.startswith('Quick save transfer to '):
                    app.logger.info('Disregarding outgoing save transfer transaction; %s ($%s %s)', ID, Amount[2], Amount[1])
                    return False
                FireflyBase['source_id'] = FireflyAccountID
                FireflyBase['destination_name'] = Category or Description
                FireflyBase['type'] = 'withdrawal'
            # Deposit.
            else:
                FireflyBase['destination_id'] = FireflyAccountID
                if Description == 'Round Up' or Description.startswith('Quick save transfer from '):
                    Category = 'Savings'
                    FireflyBase['source_id'] = Accounts[Checking]
                    FireflyBase['type'] = 'transfer'
                else:
                    if Description == 'Interest':
                        Category = 'Interest'
                    FireflyBase['source_name'] = Category or Description
                    FireflyBase['type'] = 'deposit'

        Tags = []
        if Category not in {'Savings', 'Interest'}:
            Tags.append(Description)
        for Tag in UpBase['relationships']['tags']['data']:
            Tags.append(Tag['id'])
        FireflyBase['tags'] = Tags

        # Category.
        if Category:
            FireflyBase['category_name'] = Category

        # Notes.
        Notes = []
        if UpBase['attributes']['message']:
            Notes.append(UpBase['attributes']['message'])
        if UpBase['attributes']['rawText']:
            Notes.append(UpBase['attributes']['rawText'])
        if Notes:
            FireflyBase['notes'] = '\n'.join(Notes)

    # Amounts.
    # Withdrawals require a positive number in Firefly.
    FireflyBase['amount'] = str(abs(Amount[2]))
    if ForeignAmount:
        FireflyBase['foreignAmount'] = str(abs(ForeignAmount[2]))

    JSON = json.dumps(Trans)
    JSON = JSON.encode()

    # Do upload.
    URL = '{}/api/v1/transactions'.format(os.environ['FIREFLY_BASEURL'])
    if not FireflyID:
        # New.
        PerformRequest(URL, os.environ['FIREFLY_PAT'], Accept = 'application/vnd.api+json', Method = 'POST', IsJSON = True, Data = JSON)
        app.logger.info('Transaction %s added.', ID)
    else:
        # Update.
        URL += '/{}'.format(FireflyID)
        PerformRequest(URL, os.environ['FIREFLY_PAT'], Accept = 'application/vnd.api+json', Method = 'PUT', IsJSON = True, Data = JSON)
        app.logger.info('Transaction %s updated.', ID)

    return True

""" Debuging route. """
def CheckDebug():
    AuthHeader = request.headers.get('Authorization')
    Token = os.environ.get('DEBUG_PAT')
    if not (AuthHeader and Token and AuthHeader == 'Bearer ' + Token):
        abort(403)

@app.route('/get/<ID>')
def get(ID):
    CheckDebug()
    URL = 'https://api.up.com.au/api/v1/transactions/' + ID
    Data = PerformRequest(URL, os.environ['UPBANK_PAT'], IsJSON = True)
    if not Data[0]:
        return 'FAILED TO DOWNLOAD', 500
    if not HandleTransaction('TRANSACTION_SETTLED', Data[0]):
        return 'ERROR', 500
    return 'OK'

@app.route('/delete/<ID>')
def delete(ID):
    CheckDebug()
    if not DeleteTransaction(ID):
        return 'ERROR', 500
    return 'OK'

""" Primary route. """
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
    # API Doc; https://developer.up.com.au/#callback_post_webhookURL
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

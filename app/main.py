#!/usr/bin/env python3
from flask import Flask, request, abort
import logging
import os
import hmac
import urllib.request
import json
# For the CLI.
import click
import urllib.parse

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

Timeout = int(os.environ.get('REQUEST_TIMEOUT', 10))
if Timeout > 30:
    raise Exception('Timeout is larger than what Up recommends.')

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
        app.logger.exception('Failed to extract Firefly transaction ID from Firefly\'s search results for external_id; %s', ID)
        return

    try:
        Splits = JSON['data'][0]['attributes']['transactions']
    except Exception:
        app.logger.exception('Failed to extract splits from Firefly\'s search results for Firefly transaction ID; %s', FireflyID)
        return
    if len(Splits) > 1:
        app.logger.warning('Firefly transaction ID %s has multiple splits. Using the first.', FireflyID)

    try:
        JournalID = Splits[0]['transaction_journal_id']
    except Exception:
        app.logger.exception('Failed to extract split journal ID from Firefly\'s search results for Firefly transaction ID; %s', FireflyID)
        return

    return FireflyID, JournalID

def DeleteTransaction(ID):
    # Search for the Up ID in Firefly.
    IDs = SearchFirefly(ID)
    if not IDs:
        return False

    # API Doc; https://api-docs.firefly-iii.org/#/transactions/deleteTransaction
    URL = '{}/api/v1/transactions/{}'.format(os.environ['FIREFLY_BASEURL'], IDs[0])
    Data = PerformRequest(URL, os.environ['FIREFLY_PAT'], None, 'DELETE')
    if not Data[0] and Data[1] != None:
        return False

    app.logger.info('Successfully deleted Firefly transaction %s (Up ID %s)', IDs[0], ID)
    return True

def HandleAmount(Amount):
    BasePrice = float(Amount['value'])
    return Amount['value'], Amount['currencyCode'], BasePrice

# Due to Firefly issue #3338, the time part of dates is removed on edits.
# Therefore, to keep transactions in the same order, we'll strip them off here.
def HandleDate(DateString):
    TimeStart = DateString.find('T')
    if TimeStart < 0:
        return DateString
    return DateString[:TimeStart]

def HandleTransaction(Type, UpBase):
    # Schema Doc; https://developer.up.com.au/#get_transactions_id

    try:
        ID = UpBase['id']
    except Exception:
        app.logger.exception('Transaction is missing an ID.')
        return False

    # Create the transaction.
    # API Doc; https://api-docs.firefly-iii.org/#/transactions/storeTransaction
    Trans = {'transactions': [ {'external_id': ID} ]}
    FireflyBase = Trans['transactions'][0]

    # Update if we already have it.
    FireflyID = None
    if Type == 'TRANSACTION_SETTLED':
        IDs = SearchFirefly(ID)
        if IDs:
            FireflyID = IDs[0]
            FireflyBase['transaction_journal_id'] = IDs[1]

    # Amounts.
    Amount = HandleAmount(UpBase['attributes']['amount'])
    ForeignAmount = None
    if UpBase['attributes']['foreignAmount']:
        ForeignAmount = HandleAmount(UpBase['attributes']['foreignAmount'])

    # Cashback.
    if UpBase['attributes']['cashback']:
        CashbackAmount = HandleAmount(UpBase['attributes']['cashback']['amount'])
        NewAmount = Amount[2] + CashbackAmount[2]
        if NewAmount == 0:
            app.logger.info('Disregarding full cashback transaction; %s ($%s %s). Reason; %s', ID, Amount[2], Amount[1],
                UpBase['attributes']['cashback']['description'])
            return False
        Amount = (Amount[0], Amount[1], NewAmount)

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
            # As we receive two transactions (incoming & outgoing) from Up, we'll disregard the outgoing.
            if Amount[2] < 0:
                app.logger.info('Disregarding outgoing transfer transaction; %s ($%s %s)', ID, Amount[2], Amount[1])
                return False
            DestAccount = UpBase['relationships']['transferAccount']['data']['id']
            if DestAccount not in Accounts:
                raise Exception('Transaction {} has an unknown destination account; {}'.format(ID, DestAccount))
            FireflyBase['source_id'] = Accounts[DestAccount]
            FireflyBase['destination_id'] = FireflyAccountID
            FireflyBase['type'] = 'transfer'
        else:
            # Withdrawal.
            if Amount[2] < 0:
                FireflyBase['source_id'] = FireflyAccountID
                FireflyBase['destination_name'] = Category or Description
                FireflyBase['type'] = 'withdrawal'
            # Deposit.
            else:
                FireflyBase['destination_id'] = FireflyAccountID
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
        Return = PerformRequest(URL, os.environ['FIREFLY_PAT'], Accept = 'application/vnd.api+json', Method = 'POST', IsJSON = True, Data = JSON)
        if not Return[0]:
            return False
        app.logger.info('Transaction %s added.', ID)
    else:
        # Update.
        URL += '/{}'.format(FireflyID)
        Return = PerformRequest(URL, os.environ['FIREFLY_PAT'], Accept = 'application/vnd.api+json', Method = 'PUT', IsJSON = True, Data = JSON)
        if not Return[0]:
            return False
        app.logger.info('Transaction %s updated.', ID)

    return True

""" Command line interface. """
@app.cli.command('get')
@click.argument('ids', type = click.UUID, nargs = -1)
def get(ids):
    """ Get transactions with Up transaction IDs. """
    Count = 0
    for ID in ids:
        URL = 'https://api.up.com.au/api/v1/transactions/' + str(ID)
        Data = PerformRequest(URL, os.environ['UPBANK_PAT'], IsJSON = True)
        if not Data[0]:
            continue
        Count += HandleTransaction('TRANSACTION_SETTLED', Data[0]['data'])
    click.echo(f"Obtained {Count} transaction(s).")
    return Count

@app.cli.command('delete')
@click.argument('ids', type = click.UUID, nargs = -1)
def delete(ids):
    """ Delete transactions with Up transaction IDs. """
    Count = 0
    for ID in ids:
        Count += DeleteTransaction(ID)
    click.echo(f"Deleted {Count} transaction(s).")
    return Count

@app.cli.command('getall')
@click.option('-a', '--account-id', type = click.UUID, help = 'Limit to only this account\'s tranactions.')
@click.option('-s', '--since', type = click.DateTime(), help = 'Only transactions since this timestamp.')
@click.option('-u', '--until', type = click.DateTime(), help = 'Only transactions until this timestamp.')
@click.option('-o', '--output-only', is_flag = True, help = 'Print Up transactions IDs only. Don\'t add to Firefly.')
def getaccount(account_id, since, until, output_only):
    """ Obtains all transactions. """
    if not account_id:
        URL = 'https://api.up.com.au/api/v1/transactions'
    else:
        URL = 'https://api.up.com.au/api/v1/accounts/{}/transactions'.format(account_id)

    if since or until:
        if since and until and since > until:
            raise click.ClickException('Since ({}) is later than Until ({}).'.format(since, until))
        Params = {}
        if since:
            Params['filter[since]'] = since.astimezone().isoformat(timespec = 'seconds')
        if until:
            Params['filter[until]'] = until.astimezone().isoformat(timespec = 'seconds')
        Params = urllib.parse.urlencode(Params)
        URL += '?' + Params

    while URL:
        Data = PerformRequest(URL, os.environ['UPBANK_PAT'], IsJSON = True)
        if not Data[0]:
            click.echo("Failed to download transactions.")
            return 1

        JSON = Data[0]

        for Transaction in JSON['data']:
            if output_only:
                click.echo(Transaction['id'])
            else:
                HandleTransaction('TRANSACTION_SETTLED', Transaction)

        URL = JSON['links']['next']

    return 1

""" Primary route. """
def CheckMessageSecure():
    AuthHeader = request.headers.get('X-Up-Authenticity-Signature')
    if not AuthHeader:
        app.logger.warning('Missing X-Up-Authenticity-Signature header.')
        abort(403)

    Body = request.data
    if not Body:
        app.logger.warning('Missing body.')
        abort(403)

    HMAC = hmac.new(os.environb[b'UPBANK_SECRET'], Body, 'sha256')
    Digest = HMAC.hexdigest()
    if not hmac.compare_digest(Digest, AuthHeader):
        app.logger.error('HMAC didn\'t match; %s != %s', Digest, AuthHeader)
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
            HandleTransaction(Type, Data[0]['data'])
        except Exception:
            app.logger.exception('Failed while processing %s transaction.', Type)
        app.logger.info('Received a %s message to process.', Type)

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
        app.logger.info('Received a delete message for ID; %s', TransactionID)

    else:
        app.logger.error('Unexpected resource event type; %s', Type)
        abort(400)

    return 'THANKS'

if __name__ == '__main__':
    app.run(host = '0.0.0.0', port = 80)

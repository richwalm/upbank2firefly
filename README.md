# Up Bank to Firefly III Converter

## Description

This is a [Python 3](https://www.python.org/) [Flask application](https://palletsprojects.com/p/flask/) to process [Up Bank's API webhooks](https://developer.up.com.au/#webhooks), allowing transactions from [Up Bank](https://up.com.au/) to be automatically added to [Firefly III](https://www.firefly-iii.org/).

It also includes a command line interface so transactions can be obtained and deleted manually.

## Install

Although it can be run directly if Flask is installed, it's been designed to work as a [Docker](https://www.docker.com/) container using [Docker Compose](https://docs.docker.com/compose/).

When using the provided `docker-compose.yml` file, core settings are to be provided in the `.env` file. Please see the included [`.env.template`](.env.template) for an initial template.

Once settings have been configured, the container can be started with;
```
docker-compose build && docker-compose up -d
```

It's recommended that Firefly is running on the same host or network as Up Bank requests that webhook responses are performed quickly. No asynchronously processing is currently performed.

### Reverse Proxy

If your Firefly III install is publicly available behind a reverse proxy, I would suggest placing it under the same host using an unused path.
This way you wouldn't need to manage separate HTTPS certificates.

#### Nginx Example

```
location /upbank2firefly/ {
   proxy_pass http://127.0.0.1:8083/;
   proxy_redirect     off;
   proxy_set_header   Host                 $host;
   proxy_set_header   X-Real-IP            $remote_addr;
   proxy_set_header   X-Forwarded-For      $proxy_add_x_forwarded_for;
   proxy_set_header   X-Forwarded-Proto    $scheme;
}
```

## Command Line Interface

The command options can be executed through Docker Compose with;
`docker-compose exec -e FLASK_APP=main upbank2firefly flask [command]`

### Get

```
Usage: flask get [OPTIONS] [IDS]...

  Get transactions with Up transaction IDs.
```

### Delete

```
Usage: flask delete [OPTIONS] [IDS]...

  Delete transactions with Up transaction IDs.
```

### Get All

```
Usage: flask getall [OPTIONS]

  Obtains all transactions.

Options:
  -a, --account-id UUID           Limit to only this account's tranactions.
  -s, --since [%Y-%m-%d|%Y-%m-%dT%H:%M:%S|%Y-%m-%d %H:%M:%S]
                                  Only transactions since this timestamp.
  -u, --until [%Y-%m-%d|%Y-%m-%dT%H:%M:%S|%Y-%m-%d %H:%M:%S]
                                  Only transactions until this timestamp.
  -o, --output-only               Print Up transactions IDs only. Don't add to Firefly.
```

## Credits

Written by Richard Walmsley \<richwalm+upbank2firefly@gmail.com\>. Released under the [ISC License](LICENSE.txt).

# Up Bank to Firefly III Converter

## Description

This is a [Python 3](https://www.python.org/) [Flask application](https://palletsprojects.com/p/flask/) to process [Up Bank's API webhooks](https://developer.up.com.au/#webhooks), allowing transactions from [Up Bank](https://up.com.au/) to be automatically added to [Firefly III](https://www.firefly-iii.org/).

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

## Credits

Written by Richard Walmsley \<richwalm+upbank2firefly@gmail.com\>. Released under the [ISC License](LICENSE.txt).

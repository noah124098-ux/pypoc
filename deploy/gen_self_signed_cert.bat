@echo off
REM Generate self-signed SSL cert for development/testing
REM For production use Let's Encrypt via certbot
openssl req -x509 -nodes -days 365 -newkey rsa:2048 ^
  -keyout deploy/pypoc.key ^
  -out deploy/pypoc.crt ^
  -subj "/C=IN/ST=Maharashtra/L=Mumbai/O=pypoc/CN=localhost"
echo SSL cert generated. Copy to nginx ssl/ directory.

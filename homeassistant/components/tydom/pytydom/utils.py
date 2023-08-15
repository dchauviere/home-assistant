import base64
import os
from requests.auth import HTTPDigestAuth
import time

# Generate 16 bytes random key for Sec-WebSocket-Keyand convert it to base64
def generate_random_key():
    return base64.b64encode(os.urandom(16))

# Build the headers of Digest Authentication
def build_digest_headers(login, password, url, nonce, method = "GET", realm = "protected area"):
    """
    realm : 'protected area' for local access
            'ServiceMedia' via remote server
    """
    digestAuth = HTTPDigestAuth(login, password)
    chal = dict()
    chal["nonce"] = nonce[2].split('=', 1)[1].split('"')[1]
    chal["realm"] = realm
    chal["qop"] = "auth"
    digestAuth._thread_local.chal = chal
    digestAuth._thread_local.last_nonce = nonce
    digestAuth._thread_local.nonce_count = 1
    return digestAuth.build_digest_header(method, url)

def generate_request_id():
  return str(int(time.time()*1000000))

def parseData(data):
  lines = data.split("\r\n")
  result = ""
  for i in range(0, len(lines)):
    if i % 2:
        result += lines[i]
  return result
 
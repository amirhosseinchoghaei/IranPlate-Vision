"""
Run Flask with a self-signed HTTPS certificate
for local network / mobile camera access.
"""
import os, ipaddress, datetime
from cryptography import x509
from cryptography.x509.oid import NameOID
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CERT_FILE = os.path.join(BASE_DIR, 'cert.pem')
KEY_FILE  = os.path.join(BASE_DIR, 'key.pem')

def make_cert():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, u'plate-local')])
    san = x509.SubjectAlternativeName([
        x509.DNSName(u'localhost'),
        x509.IPAddress(ipaddress.IPv4Address('127.0.0.1')),
        x509.IPAddress(ipaddress.IPv4Address('0.0.0.0')),
    ])
    now = datetime.datetime.utcnow()
    cert = (
        x509.CertificateBuilder()
        .subject_name(name).issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + datetime.timedelta(days=3650))
        .add_extension(san, critical=False)
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    with open(KEY_FILE, 'wb') as f:
        f.write(key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption()
        ))
    with open(CERT_FILE, 'wb') as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))
    print(f'Certificate generated: {CERT_FILE} | گواهی ساخته شد')

if not os.path.exists(CERT_FILE) or not os.path.exists(KEY_FILE):
    print('Generating self-signed certificate... | در حال ساخت گواهی self-signed')
    make_cert()

# Show local network IP candidates
import socket
hostname = socket.gethostname()
try:
    local_ip = socket.gethostbyname(hostname)
except:
    local_ip = '?'

print(f'\n  https://localhost:5000')
print(f'  https://{local_ip}:5000  <- open this on your mobile | این آدرس را روی موبایل باز کنید')
print('\n  Browser may show "Not Secure" for self-signed certs; use Advanced -> Proceed.\n'
      '  ممکن است مرورگر هشدار بدهد؛ از Advanced -> Proceed استفاده کنید.\n')

import app as flask_app
flask_app.app.run(
    debug=False,
    host='0.0.0.0',
    port=5000,
    ssl_context=(CERT_FILE, KEY_FILE),
    use_reloader=False,
)

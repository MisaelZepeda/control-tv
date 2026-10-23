"""Certificados del puente: una autoridad (CA) local y el certificado HTTPS del puente.

La webapp vive en GitHub Pages (https), y el navegador solo la deja hablar con el puente por https. Para que el
celular confíe en el puente, se instala una vez la CA local (ca.crt). Esa CA tiene *restricción de nombres*: solo
puede firmar certificados para direcciones privadas (10.x, 172.16-31.x, 192.168.x, 127.x) y "localhost", así que
aunque alguien obtuviera su llave no podría hacerse pasar por ningún sitio de internet.

Todo se guarda en bridge/data/ (fuera del repositorio). La llave de la CA nunca sale de esta PC.
"""
import datetime
import ipaddress
import os

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

PRIVATE_NETS = [ipaddress.ip_network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "127.0.0.0/8")]
CA_DAYS = 3650
LEAF_DAYS = 397          # Apple no acepta certificados de servidor de más de 398 días
RENEW_BEFORE_DAYS = 30


def _now():
    return datetime.datetime.now(datetime.timezone.utc)


def _write_key(path, key):
    with open(path, "wb") as f:
        f.write(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                  serialization.NoEncryption()))
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _write_cert(path, cert):
    with open(path, "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))


def ensure_ca(data_dir):
    """La CA local: la carga si existe, si no la crea. Devuelve (llave, certificado)."""
    key_path = os.path.join(data_dir, "ca.key")
    cert_path = os.path.join(data_dir, "ca.crt")
    if os.path.exists(key_path) and os.path.exists(cert_path):
        with open(key_path, "rb") as f:
            key = serialization.load_pem_private_key(f.read(), password=None)
        with open(cert_path, "rb") as f:
            cert = x509.load_pem_x509_certificate(f.read())
        return key, cert
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Control TV - CA local"),
                      x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Control TV")])
    now = _now()
    permitted = [x509.IPAddress(net) for net in PRIVATE_NETS] + [x509.DNSName("localhost")]
    cert = (x509.CertificateBuilder()
            .subject_name(name).issuer_name(name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(minutes=5))
            .not_valid_after(now + datetime.timedelta(days=CA_DAYS))
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
            .add_extension(x509.KeyUsage(digital_signature=True, content_commitment=False, key_encipherment=False,
                                         data_encipherment=False, key_agreement=False, key_cert_sign=True,
                                         crl_sign=True, encipher_only=False, decipher_only=False), critical=True)
            .add_extension(x509.NameConstraints(permitted_subtrees=permitted, excluded_subtrees=None), critical=True)
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
            .sign(key, hashes.SHA256()))
    os.makedirs(data_dir, exist_ok=True)
    _write_key(key_path, key)
    _write_cert(cert_path, cert)
    return key, cert


def _leaf_covers(cert, ips):
    try:
        san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    except x509.ExtensionNotFound:
        return False
    have = set(san.get_values_for_type(x509.IPAddress))
    fresh = cert.not_valid_after_utc - _now() > datetime.timedelta(days=RENEW_BEFORE_DAYS)
    return fresh and all(ipaddress.ip_address(ip) in have for ip in ips)


def ensure_server_cert(data_dir, ips):
    """El certificado https del puente para estas IPs (y localhost). Lo vuelve a emitir si cambió la IP de la PC
    o está por vencer; la CA sigue siendo la misma, así que el celular no tiene que instalar nada otra vez.
    Devuelve las rutas (certificado, llave)."""
    ips = [ip for ip in ips if any(ipaddress.ip_address(ip) in net for net in PRIVATE_NETS)]
    cert_path = os.path.join(data_dir, "server.crt")
    key_path = os.path.join(data_dir, "server.key")
    if os.path.exists(cert_path) and os.path.exists(key_path):
        with open(cert_path, "rb") as f:
            if _leaf_covers(x509.load_pem_x509_certificate(f.read()), ips):
                return cert_path, key_path
    ca_key, ca_cert = ensure_ca(data_dir)
    key = ec.generate_private_key(ec.SECP256R1())
    now = _now()
    san = [x509.IPAddress(ipaddress.ip_address(ip)) for ip in ips] + [x509.DNSName("localhost")]
    cert = (x509.CertificateBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, ips[0] if ips else "localhost")]))
            .issuer_name(ca_cert.subject)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(minutes=5))
            .not_valid_after(now + datetime.timedelta(days=LEAF_DAYS))
            .add_extension(x509.SubjectAlternativeName(san), critical=False)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.KeyUsage(digital_signature=True, content_commitment=False, key_encipherment=False,
                                         data_encipherment=False, key_agreement=False, key_cert_sign=False,
                                         crl_sign=False, encipher_only=False, decipher_only=False), critical=True)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
            .sign(ca_key, hashes.SHA256()))
    _write_key(key_path, key)
    with open(cert_path, "wb") as f:
        # La cadena completa: el certificado del puente y después el de la CA.
        f.write(cert.public_bytes(serialization.Encoding.PEM) + ca_cert.public_bytes(serialization.Encoding.PEM))
    return cert_path, key_path

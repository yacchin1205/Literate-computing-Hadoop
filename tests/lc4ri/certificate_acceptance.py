"""Check real TLS connections and truststores independently of certificate operations."""
import hashlib
import json
import os
import re
import shlex
import socket
import ssl
import subprocess
import unittest


HTTPS_ENDPOINTS = [
    ('nn1', 8481), ('nn2', 8481), ('dn1', 8481),
    ('nn1', 50470), ('nn2', 50470),
    ('nn1', 50475), ('nn2', 50475), ('dn1', 50475),
    ('nn1', 8088), ('nn2', 8088),
    ('nn1', 8044), ('nn2', 8044), ('dn1', 8044),
    ('nn1', 19890), ('nn1', 8190),
]


def remote_read(results, address, command, input_text=None):
    result = subprocess.run(['ssh', *shlex.split(os.environ['ANSIBLE_SSH_ARGS']),
                             'ansible@' + address, command], input=input_text, text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
    with (results / 'certificate-observations.log').open('a') as stream:
        stream.write(address + ': ' + command + '\n' + result.stdout + result.stderr + '\n')
    result.check_returncode()
    return result.stdout


def peer_certificate(context, address, hostname, port):
    with socket.create_connection((address, port), timeout=30) as connection:
        with context.wrap_socket(connection, server_hostname=hostname) as tls:
            return tls.getpeercert(binary_form=True)


def capture_certificates(results, stage, addresses):
    pem = remote_read(results, addresses['nn1'], 'cat /etc/hadoop/conf/ca-certificates.pem')
    authority_file = results / ('ca-' + stage + '.pem')
    authority_file.write_text(pem)
    context = ssl.create_default_context(cafile=str(authority_file))
    authorities = [hashlib.sha256(value).hexdigest() for value in context.get_ca_certs(binary_form=True)]
    connections = {}
    for hostname, port in HTTPS_ENDPOINTS:
        certificate = peer_certificate(context, addresses[hostname], hostname, port)
        public_key = subprocess.run(['openssl', 'x509', '-pubkey', '-noout'],
                                    input=ssl.DER_cert_to_PEM_cert(certificate), text=True,
                                    stdout=subprocess.PIPE, check=True).stdout
        connections[hostname + ':' + str(port)] = dict(
            certificate_sha256=hashlib.sha256(certificate).hexdigest(),
            public_key_sha256=hashlib.sha256(public_key.encode()).hexdigest())
    snapshot = dict(authorities=authorities, authority_file=str(authority_file), connections=connections)
    (results / ('certificate-' + stage + '.json')).write_text(json.dumps(snapshot, indent=2) + '\n')
    return snapshot


def observe_truststores(session, controls, index, text, observed):
    if text.strip() != '!ansible-playbook {playbook_dir}/restart_https_services.yml -l {target_group}':
        return
    phase = len(session.ca_trust_phases) + 1
    snapshot = capture_certificates(session.results, 'trust-phase-' + str(phase), session.ca_addresses)
    stores = {}
    for hostname, address in session.ca_addresses.items():
        output = remote_read(session.results, address,
                             'keytool -list -rfc -keystore /etc/hadoop/conf/truststore.jks',
                             os.environ['KERBEROS_TEST_PASSWORD'] + '\n')
        certificates = re.findall(r'-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----', output, re.S)
        stores[hostname] = [hashlib.sha256(ssl.PEM_cert_to_DER_cert(value)).hexdigest() for value in certificates]
    snapshot['java_truststores'] = stores
    session.ca_trust_phases.append(snapshot)
    (session.results / 'certificate-trust-phases.json').write_text(json.dumps(session.ca_trust_phases, indent=2) + '\n')


def verify_ca_renewal(session):
    before = session.ca_before
    after = capture_certificates(session.results, 'after', session.ca_addresses)
    assert len(before['authorities']) == len(after['authorities']) == 1
    assert before['authorities'] != after['authorities']
    assert len(session.ca_trust_phases) == 2
    dual, final = session.ca_trust_phases
    expected_dual = set(before['authorities'] + after['authorities'])
    assert set(dual['authorities']) == expected_dual
    assert final['authorities'] == after['authorities']
    for roots in dual['java_truststores'].values():
        assert set(roots) == expected_dual, roots
    for roots in final['java_truststores'].values():
        assert roots == after['authorities'], roots
    assert dual['connections'] == before['connections']
    for endpoint, evidence in after['connections'].items():
        assert evidence['certificate_sha256'] != before['connections'][endpoint]['certificate_sha256'], endpoint
        assert evidence['public_key_sha256'] != before['connections'][endpoint]['public_key_sha256'], endpoint
    old_context = ssl.create_default_context(cafile=before['authority_file'])
    rejections = {}
    for hostname, port in HTTPS_ENDPOINTS:
        with unittest.TestCase().assertRaises(ssl.SSLCertVerificationError) as error:
            peer_certificate(old_context, session.ca_addresses[hostname], hostname, port)
        assert error.exception.verify_code in (19, 20, 21), error.exception
        rejections[hostname + ':' + str(port)] = error.exception.verify_message
    spark_book = json.loads((session.results / 'T12d-ca-renewed-working.ipynb').read_text())
    spark_output = '\n'.join(''.join(item.get('text', []))
                             for cell in spark_book['cells'] for item in cell.get('outputs', []))
    assert 'Lines with a: 100, lines with b: 0' in spark_output, spark_output
    report = dict(before=before, after=after, trust_phases=session.ca_trust_phases,
                  old_ca_rejections=rejections)
    (session.results / 'ca-renewal-acceptance.json').write_text(json.dumps(report, indent=2) + '\n')

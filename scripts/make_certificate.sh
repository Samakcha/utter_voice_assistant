#!/bin/bash
# Make the free "Utter Developer" code-signing certificate in your login keychain, so macOS
# keeps Utter's permissions (microphone, Accessibility, Input Monitoring) when you update it.
# install.sh offers to run this once. It is the same as Keychain Access → Certificate
# Assistant → Create a Certificate… (Name: Utter Developer; Identity Type: Self-Signed Root;
# Certificate Type: Code Signing). No Apple account and no payment.
#
# It only adds this one certificate (and its private key) to your login keychain; it changes
# no other setting. To remove it later: Keychain Access → login → My Certificates →
# Utter Developer → Delete.
#
#   scripts/make_certificate.sh                  add it to the login keychain
#   scripts/make_certificate.sh --files DIR      only write the certificate files (for testing)

set -euo pipefail

NAME="Utter Developer"
KEYCHAIN="$HOME/Library/Keychains/login.keychain-db"
FILES_ONLY=""
if [ "${1:-}" = "--files" ]; then
    FILES_ONLY="${2:?--files needs a folder}"
fi

if [ -z "$FILES_ONLY" ] && security find-identity -p codesigning 2>/dev/null | grep -q "\"$NAME\""; then
    echo "The \"$NAME\" certificate is already in your keychain."
    exit 0
fi

WORK="${FILES_ONLY:-$(mktemp -d)}"
mkdir -p "$WORK"
if [ -z "$FILES_ONLY" ]; then
    trap 'rm -rf "$WORK"' EXIT
fi

cat > "$WORK/cert.conf" <<EOF
[req]
distinguished_name = dn
x509_extensions = ext
prompt = no
[dn]
CN = $NAME
[ext]
basicConstraints = critical, CA:false
keyUsage = critical, digitalSignature
extendedKeyUsage = critical, codeSigning
subjectKeyIdentifier = hash
EOF

# macOS's own openssl (LibreSSL): its .p12 files are the kind the keychain can import
OPENSSL=/usr/bin/openssl
"$OPENSSL" req -x509 -newkey rsa:2048 -sha256 -days 3650 -nodes \
    -config "$WORK/cert.conf" -keyout "$WORK/key.pem" -out "$WORK/cert.pem" 2>/dev/null >/dev/null
PASSWORD="$("$OPENSSL" rand -hex 16)"  # only protects the temporary .p12 file
"$OPENSSL" pkcs12 -export -name "$NAME" -inkey "$WORK/key.pem" -in "$WORK/cert.pem" \
    -out "$WORK/utter.p12" -passout "pass:$PASSWORD"

if [ -n "$FILES_ONLY" ]; then
    echo "Wrote $WORK/cert.pem and $WORK/utter.p12 (not added to any keychain)."
    exit 0
fi

# -T: codesign may use the key. The first build may still ask once whether codesign can use
# "Utter Developer": click Always Allow (your Mac login password may be asked).
security import "$WORK/utter.p12" -k "$KEYCHAIN" -P "$PASSWORD" -T /usr/bin/codesign >/dev/null
rm -f "$WORK/key.pem" "$WORK/utter.p12"

if security find-identity -p codesigning 2>/dev/null | grep -q "\"$NAME\""; then
    echo "Added the \"$NAME\" certificate to your login keychain."
else
    echo "The certificate was added, but macOS doesn't list it for code signing." >&2
    exit 1
fi

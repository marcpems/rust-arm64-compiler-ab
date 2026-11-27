#!/bin/sh

# ignore-tidy-linelength

set -ex

case "$(uname -m)" in
    x86_64)
        url="https://github.com/mozilla/sccache/releases/download/v0.20.1/sccache-v0.20.1-x86_64-unknown-linux-musl.tar.gz"
        digest=c9f6c5a3ae84d9da0956b28c357a365827bda33f3383647fc0b67484c74a5ea9
        ;;
    aarch64)
        url="https://github.com/mozilla/sccache/releases/download/v0.20.1/sccache-v0.20.1-aarch64-unknown-linux-musl.tar.gz"
        digest=505324daad5fcbb1a3cab5d57d26ef42baf4d10b236df7de93a008196f1f2230
        ;;
    *)
        echo "unsupported architecture: $(uname -m)"
        exit 1
esac

curl -fLo sccache.tar.gz "${url}"
echo "$digest  sccache.tar.gz" | sha256sum -c -
tar -xvf sccache.tar.gz --strip-components 1
mv sccache /usr/local/bin/sccache
chmod +x /usr/local/bin/sccache

#!/usr/bin/env bash
# Install Terraform plus the Google provider without registry.terraform.io
# (useful in sandboxes where only releases.hashicorp.com is reachable).
set -euo pipefail
TF_VERSION="${TF_VERSION:-1.9.8}"
PROVIDER_VERSION="${PROVIDER_VERSION:-7.46.2}"
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT

if ! command -v terraform >/dev/null; then
  curl -sSfLo "$TMP/tf.zip" "https://releases.hashicorp.com/terraform/${TF_VERSION}/terraform_${TF_VERSION}_linux_amd64.zip"
  unzip -oq "$TMP/tf.zip" terraform -d /usr/local/bin
fi

MIRROR="$HOME/.terraform.d/plugins"
DEST="$MIRROR/registry.terraform.io/hashicorp/google/${PROVIDER_VERSION}/linux_amd64"
if [[ ! -d "$DEST" ]]; then
  mkdir -p "$DEST"
  curl -sSfLo "$TMP/p.zip" "https://releases.hashicorp.com/terraform-provider-google/${PROVIDER_VERSION}/terraform-provider-google_${PROVIDER_VERSION}_linux_amd64.zip"
  unzip -oq "$TMP/p.zip" -d "$DEST"
fi

cat > "$HOME/.terraformrc" <<RC
provider_installation {
  filesystem_mirror { path = "$MIRROR" }
  direct { exclude = ["registry.terraform.io/hashicorp/google"] }
}
disable_checkpoint = true
RC
terraform version | head -1

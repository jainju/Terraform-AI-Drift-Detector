#!/bin/bash

echo "==> Installing Terraform..."

# Install dependencies
sudo apt-get update -y
sudo apt-get install -y curl unzip

# Get latest terraform version
TERRAFORM_VERSION="1.8.5"

# Download binary
echo "==> Downloading Terraform v${TERRAFORM_VERSION}..."
curl -LO "https://releases.hashicorp.com/terraform/${TERRAFORM_VERSION}/terraform_${TERRAFORM_VERSION}_linux_amd64.zip"

# Unzip
echo "==> Extracting..."
unzip -o terraform_${TERRAFORM_VERSION}_linux_amd64.zip

# Move to PATH
echo "==> Installing to /usr/local/bin..."
sudo mv terraform /usr/local/bin/

# Cleanup
rm -f terraform_${TERRAFORM_VERSION}_linux_amd64.zip

# Verify
echo "==> Done!"
terraform version
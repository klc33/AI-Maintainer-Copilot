# Terraform Basics

## What is Terraform?
Terraform is an open-source infrastructure as code (IaC) tool created by HashiCorp. It allows you to define and provision data center infrastructure using a declarative configuration language.

## Key Concepts
- **Provider**: A plugin that interacts with a specific cloud service (AWS, Azure, GCP, etc.)
- **Resource**: A component of your infrastructure (e.g., EC2 instance, S3 bucket)
- **Module**: A container for multiple resources that are used together
- **State**: Terraform's record of the infrastructure it manages
- **Plan**: A preview of changes before applying them
- **Apply**: Executing the planned changes

## Common Commands
```bash
terraform init          # Initialize a working directory
terraform plan          # Show changes required
terraform apply         # Apply the changes
terraform destroy       # Destroy the infrastructure
terraform fmt           # Format code
terraform validate      # Validate the configuration
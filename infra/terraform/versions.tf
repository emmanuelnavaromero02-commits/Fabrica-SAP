terraform {
  required_version = ">= 1.5"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = var.aws_region
  # Tags obligatorios aplicados a TODO recurso (decisión #8 del brief).
  default_tags {
    tags = var.tags
  }
}

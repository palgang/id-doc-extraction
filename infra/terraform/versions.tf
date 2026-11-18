terraform {
  required_version = ">= 1.6"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = ">= 6.0, < 8.0"
    }
  }
  # State lives in GCS so every deploy (Cloud Shell, CI, a laptop) sees the same resources.
  # scripts/deploy.sh creates the bucket and passes it with -backend-config.
  backend "gcs" {}
}

provider "google" {
  project = var.project_id
  region  = var.region
  # Bill API quota to the project, so user credentials (e.g. Cloud Shell) work
  # with APIs that require a quota project, such as Document AI.
  user_project_override = true
  billing_project       = var.project_id
}

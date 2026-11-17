terraform {
  required_version = ">= 1.6"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = ">= 6.0, < 8.0"
    }
  }
  # For team use, store state in GCS:
  # backend "gcs" { bucket = "<project>-tfstate"; prefix = "id-extractor" }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

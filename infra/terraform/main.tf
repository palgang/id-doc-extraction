locals {
  apis = [
    "artifactregistry.googleapis.com",
    "cloudbuild.googleapis.com",
    "documentai.googleapis.com",
    "aiplatform.googleapis.com",
    "run.googleapis.com",
    "eventarc.googleapis.com",
    "pubsub.googleapis.com",
    "storage.googleapis.com",
    "iam.googleapis.com",
    "logging.googleapis.com",
  ]
  deploy_service = var.image != ""
  # Processor id is the last segment of projects/<p>/locations/<l>/processors/<id>.
  docai_processor_id = element(reverse(split("/", google_document_ai_processor.ocr.id)), 0)
}

data "google_project" "this" {}

resource "google_project_service" "apis" {
  for_each           = toset(local.apis)
  service            = each.value
  disable_on_destroy = false
}

# --------------------------------------------------------------------------- build

resource "google_artifact_registry_repository" "repo" {
  repository_id = var.service_name
  location      = var.region
  format        = "DOCKER"
  depends_on    = [google_project_service.apis]
}

resource "google_service_account" "build" {
  account_id   = "${var.service_name}-build"
  display_name = "ID extractor image builds"
}

resource "google_project_iam_member" "build" {
  for_each = toset(["roles/logging.logWriter", "roles/artifactregistry.writer", "roles/storage.objectViewer"])
  project  = var.project_id
  role     = each.value
  member   = "serviceAccount:${google_service_account.build.email}"
}

# --------------------------------------------------------------------------- Document AI

resource "google_document_ai_processor" "ocr" {
  location     = var.docai_location
  display_name = "${var.service_name}-ocr"
  type         = "OCR_PROCESSOR"
  depends_on   = [google_project_service.apis]
}

# --------------------------------------------------------------------------- storage

resource "google_storage_bucket" "input" {
  name                        = "${var.project_id}-${var.service_name}-in"
  location                    = var.region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = true
  lifecycle_rule {
    condition { age = var.retention_days }
    action { type = "Delete" }
  }
  depends_on = [google_project_service.apis]
}

resource "google_storage_bucket" "output" {
  name                        = "${var.project_id}-${var.service_name}-out"
  location                    = var.region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = true
  lifecycle_rule {
    condition { age = var.retention_days }
    action { type = "Delete" }
  }
  depends_on = [google_project_service.apis]
}

# --------------------------------------------------------------------------- runtime identity

resource "google_service_account" "run" {
  account_id   = "${var.service_name}-run"
  display_name = "ID extractor Cloud Run runtime"
}

resource "google_project_iam_member" "run" {
  for_each = toset(["roles/documentai.apiUser", "roles/aiplatform.user", "roles/logging.logWriter"])
  project  = var.project_id
  role     = each.value
  member   = "serviceAccount:${google_service_account.run.email}"
}

resource "google_storage_bucket_iam_member" "run_input" {
  bucket = google_storage_bucket.input.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.run.email}"
}

resource "google_storage_bucket_iam_member" "run_output" {
  bucket = google_storage_bucket.output.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.run.email}"
}

# --------------------------------------------------------------------------- Cloud Run

resource "google_cloud_run_v2_service" "api" {
  count               = local.deploy_service ? 1 : 0
  name                = var.service_name
  location            = var.region
  ingress             = "INGRESS_TRAFFIC_ALL"
  deletion_protection = false

  template {
    service_account                  = google_service_account.run.email
    timeout                          = "120s"
    max_instance_request_concurrency = 8
    scaling {
      min_instance_count = 0
      max_instance_count = 5
    }
    containers {
      image = var.image
      resources {
        limits = { cpu = "1", memory = "1Gi" }
        # Request-based billing: CPU only while a request is handled, so an idle
        # instance costs nothing and the service scales to zero (min_instance_count = 0).
        cpu_idle          = true
        startup_cpu_boost = true # shorter cold starts
      }
      env {
        name  = "GCP_PROJECT_ID"
        value = var.project_id
      }
      env {
        name  = "DOCAI_LOCATION"
        value = var.docai_location
      }
      env {
        name  = "DOCAI_PROCESSOR_ID"
        value = local.docai_processor_id
      }
      env {
        name  = "VERTEX_LOCATION"
        value = var.vertex_location
      }
      env {
        name  = "GEMINI_MODEL"
        value = var.gemini_model
      }
      env {
        name  = "OUTPUT_BUCKET"
        value = google_storage_bucket.output.name
      }
      startup_probe {
        http_get { path = "/healthz" }
      }
    }
  }
  depends_on = [google_project_iam_member.run]
}

resource "google_cloud_run_v2_service_iam_member" "invokers" {
  for_each = local.deploy_service ? toset(var.invoker_members) : toset([])
  name     = google_cloud_run_v2_service.api[0].name
  location = var.region
  role     = "roles/run.invoker"
  member   = each.value
}

# --------------------------------------------------------------------------- Eventarc (bucket -> Cloud Run)

resource "google_service_account" "trigger" {
  account_id   = "${var.service_name}-trigger"
  display_name = "ID extractor Eventarc trigger"
}

resource "google_project_iam_member" "trigger_receiver" {
  project = var.project_id
  role    = "roles/eventarc.eventReceiver"
  member  = "serviceAccount:${google_service_account.trigger.email}"
}

resource "google_cloud_run_v2_service_iam_member" "trigger_invoker" {
  count    = local.deploy_service ? 1 : 0
  name     = google_cloud_run_v2_service.api[0].name
  location = var.region
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.trigger.email}"
}

data "google_storage_project_service_account" "gcs" {
  depends_on = [google_project_service.apis]
}

resource "google_project_iam_member" "gcs_pubsub_publisher" {
  project = var.project_id
  role    = "roles/pubsub.publisher"
  member  = "serviceAccount:${data.google_storage_project_service_account.gcs.email_address}"
}

resource "google_eventarc_trigger" "upload" {
  count           = local.deploy_service ? 1 : 0
  name            = "${var.service_name}-upload"
  location        = var.region
  service_account = google_service_account.trigger.email

  matching_criteria {
    attribute = "type"
    value     = "google.cloud.storage.object.v1.finalized"
  }
  matching_criteria {
    attribute = "bucket"
    value     = google_storage_bucket.input.name
  }
  destination {
    cloud_run_service {
      service = google_cloud_run_v2_service.api[0].name
      region  = var.region
      path    = "/events/gcs"
    }
  }
  depends_on = [
    google_project_iam_member.trigger_receiver,
    google_project_iam_member.gcs_pubsub_publisher,
    google_cloud_run_v2_service_iam_member.trigger_invoker,
  ]
}

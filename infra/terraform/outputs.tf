output "service_url" {
  value = local.deploy_service ? google_cloud_run_v2_service.api[0].uri : null
}

output "image_repo" {
  value = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.repo.repository_id}"
}

output "build_service_account" {
  value = google_service_account.build.id
}

output "input_bucket" {
  value = google_storage_bucket.input.name
}

output "output_bucket" {
  value = google_storage_bucket.output.name
}

output "docai_processor" {
  value = google_document_ai_processor.ocr.id
}

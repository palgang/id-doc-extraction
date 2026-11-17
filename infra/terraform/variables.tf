variable "project_id" {
  description = "GCP project to deploy into."
  type        = string
}

variable "region" {
  description = "Region for Cloud Run, buckets and Artifact Registry. Frankfurt keeps German ID data in Germany."
  type        = string
  default     = "europe-west3"
}

variable "docai_location" {
  description = "Document AI multi-region (eu or us)."
  type        = string
  default     = "eu"
}

variable "vertex_location" {
  description = "Vertex AI location for Gemini. Use an EU region if your model supports it and you need EU-only processing."
  type        = string
  default     = "global"
}

variable "gemini_model" {
  description = "Gemini model ID on Vertex AI."
  type        = string
  default     = "gemini-3.5-flash"
}

variable "service_name" {
  type    = string
  default = "id-extractor"
}

variable "image" {
  description = "Container image for the Cloud Run service. Empty on the first (bootstrap) apply."
  type        = string
  default     = ""
}

variable "invoker_members" {
  description = "Principals allowed to call the API, e.g. [\"user:you@example.com\"]. The service is never public."
  type        = list(string)
  default     = []
}

variable "retention_days" {
  description = "Uploaded documents and results are deleted from the buckets after this many days."
  type        = number
  default     = 1
}

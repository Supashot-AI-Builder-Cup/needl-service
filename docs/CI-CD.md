# Backend CI/CD ke Cloud Run

Panduan rilis `needl-service` ke Google Cloud Run. Backend memakai Dockerfile
di root, Firestore, dan Secret Manager. Build produksi dilakukan Cloud Build
melalui `deploy/deploy.sh`; konfigurasi runtime disimpan di `deploy/env.yaml`.

Workflow `.github/workflows/deploy-cloud-run.yml` menjalankan validasi pada
pull request dan deploy otomatis setelah push ke `main`. Deploy memakai
Workload Identity Federation (WIF), tanpa service account key.

## 1. Jalankan CI sebelum rilis

Gunakan Python 3.11 atau lebih baru. Siapkan dependency dan Firestore emulator,
lalu jalankan suite:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
scripts/test.sh
```

`scripts/test.sh` memakai emulator yang berjalan di `127.0.0.1:8085`, atau
menjalankannya sendiri melalui gcloud dengan Java 21+ atau Docker. Suite berjalan
dengan sumber dan analyzer stub, tanpa memanggil API provider eksternal.

Sebelum merge, pastikan perubahan API sesuai kontrak dan dokumentasi. Deploy
hanya dari branch rilis yang sudah ditinjau.

## 2. Siapkan Google Cloud dan autentikasi GitHub

Gunakan project dengan billing aktif. Setup awal membuat Firestore Native,
Artifact Registry, service account runtime, konfigurasi TTL/index, dan secret.
Jalankan sekali dari mesin yang sudah login gcloud:

```bash
gcloud auth login
PROJECT_ID=your-project deploy/setup.sh
```

Script meminta nilai secret secara tersembunyi jika berjalan di terminal.
Secret yang dipakai aplikasi: `GEMINI_API_KEY`, `APIFY_TOKEN`, dan
`RAPIDAPI_KEY`. Secret tanpa versi aktif dilewati saat deploy.

Buat deploy service account dan WIF pool/provider. Ganti nilai contoh dengan
organisasi/repository GitHub sebenarnya:

```bash
PROJECT_ID=your-project
GITHUB_REPOSITORY=your-org/needl-service
DEPLOY_SA=github-deployer
PROJECT_NUMBER="$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')"

gcloud --project "$PROJECT_ID" iam service-accounts create "$DEPLOY_SA"
gcloud --project "$PROJECT_ID" projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:${DEPLOY_SA}@${PROJECT_ID}.iam.gserviceaccount.com" \
  --role=roles/run.admin
gcloud --project "$PROJECT_ID" projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:${DEPLOY_SA}@${PROJECT_ID}.iam.gserviceaccount.com" \
  --role=roles/run.sourceDeveloper
gcloud --project "$PROJECT_ID" projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:${DEPLOY_SA}@${PROJECT_ID}.iam.gserviceaccount.com" \
  --role=roles/serviceusage.serviceUsageConsumer
gcloud --project "$PROJECT_ID" iam service-accounts add-iam-policy-binding \
  "needl-service@${PROJECT_ID}.iam.gserviceaccount.com" \
  --member="serviceAccount:${DEPLOY_SA}@${PROJECT_ID}.iam.gserviceaccount.com" \
  --role=roles/iam.serviceAccountUser

gcloud --project "$PROJECT_ID" iam workload-identity-pools create github \
  --location=global --display-name="GitHub Actions"
gcloud --project "$PROJECT_ID" iam workload-identity-pools providers create-oidc github-provider \
  --location=global --workload-identity-pool=github \
  --display-name="GitHub provider" \
  --issuer-uri="https://token.actions.githubusercontent.com" \
  --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository" \
  --attribute-condition="assertion.repository == '${GITHUB_REPOSITORY}'"
gcloud --project "$PROJECT_ID" iam service-accounts add-iam-policy-binding \
  "${DEPLOY_SA}@${PROJECT_ID}.iam.gserviceaccount.com" \
  --role=roles/iam.workloadIdentityUser \
  --member="principalSet://iam.googleapis.com/projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/github/attribute.repository/${GITHUB_REPOSITORY}"
```

Di GitHub, buka **Settings → Secrets and variables → Actions → Variables** lalu
tambahkan repository variables berikut:

| Variable | Nilai |
|---|---|
| `GCP_PROJECT_ID` | Project ID GCP. |
| `GCP_WIF_PROVIDER` | `projects/PROJECT_NUMBER/locations/global/workloadIdentityPools/github/providers/github-provider`. |
| `GCP_DEPLOY_SERVICE_ACCOUNT` | `github-deployer@PROJECT_ID.iam.gserviceaccount.com`. |
| `GCP_REGION` | `asia-southeast2` atau region pilihan. |
| `CORS_ORIGIN` | Origin frontend, tanpa trailing slash. |

Nilai tersebut identifier/configuration, bukan kredensial. Workflow hanya
menerima identitas dari repository yang dipatok pada kondisi OIDC provider.
Untuk approval sebelum deploy, tambahkan GitHub Environment `production`, lalu
set `environment: production` pada job `deploy` dan atur required reviewers.

## 3. Atur konfigurasi runtime

Salin contoh konfigurasi, lalu isi project ID dan origin frontend:

```bash
cp deploy/env.example.yaml deploy/env.yaml
```

Atur `GOOGLE_CLOUD_PROJECT` dan `CORS_ORIGINS`. Pastikan
`NEEDL_SOURCE=live`, `NEEDL_ANALYZER=real`, provider yang dibutuhkan ada di
`SHOPEE_PROVIDERS`, dan URL RapidAPI diisi jika provider itu digunakan.
`deploy/env.yaml` diabaikan Git. Jangan masukkan nilai rahasia ke file ini;
secret runtime berasal dari Secret Manager.

## 4. Build dan deploy

Setelah workflow dan variabel GitHub disiapkan, merge ke `main` menjalankan
`deploy/deploy.sh` otomatis setelah job `validate` berhasil. Untuk deploy manual
atau rilis pertama, jalankan dari root repository:

```bash
PROJECT_ID=your-project deploy/deploy.sh
```

Script mengirim source ke Cloud Build, membangun Dockerfile, menyimpan image di
Artifact Registry (`cloud-run-source-deploy`), lalu merilis service
`needl-service`. Default region `asia-southeast2`. Override jika perlu:

```bash
PROJECT_ID=your-project REGION=asia-southeast2 deploy/deploy.sh
```

Konfigurasi Cloud Run: service account `needl-service`, 1 CPU, memori 1 GiB,
concurrency 80, timeout 300 detik, minimum 0 dan maksimum 1 instance. Satu
instance diperlukan karena job queue dan rate limiter berada di memori proses.
Cloud Run memakai CPU always allocated agar worker job tetap berjalan.

## 5. Verifikasi rilis

`deploy.sh` mencetak URL service setelah deploy. Jalankan smoke check:

```bash
PROJECT_ID=your-project deploy/smoke.sh
```

Smoke check memeriksa `/health`, konfigurasi live, CORS, dan rate limit di Cloud
Run. Untuk menguji satu job sungguhan:

```bash
PROJECT_ID=your-project deploy/smoke.sh --with-job --link 'https://shopee.co.id/...'
```

Pemeriksaan job memanggil provider sungguhan dan dapat memakai kredit Apify.
Frontend harus memakai URL service sebagai `VITE_API_URL`; origin frontend
harus tercantum di `CORS_ORIGINS`. Perubahan `CORS_ORIGINS` perlu deploy ulang.

## 6. Operasi dan rollback

```bash
gcloud run services logs read needl-service --region asia-southeast2 --limit 100
gcloud run revisions list --service needl-service --region asia-southeast2
gcloud run services update-traffic needl-service --to-revisions REVISION=100 --region asia-southeast2
```

Ganti `REVISION` dengan revisi stabil dari daftar. Untuk merotasi secret, tambahkan
versi di Secret Manager lalu jalankan ulang `deploy/deploy.sh`; Cloud Run
membaca versi `latest` saat instance mulai.

## Berkas terkait

| Berkas | Fungsi |
|---|---|
| `Dockerfile` | Image FastAPI untuk Cloud Run pada port 8080. |
| `.github/workflows/deploy-cloud-run.yml` | Validasi pull request dan deploy push ke `main`. |
| `deploy/setup.sh` | Menyiapkan layanan GCP, Firestore, service account, dan secret. |
| `deploy/deploy.sh` | Build lewat Cloud Build dan deploy ke Cloud Run. |
| `deploy/smoke.sh` | Memeriksa service Cloud Run setelah deploy. |
| `deploy/env.example.yaml` | Contoh konfigurasi non-secret runtime. |
| `scripts/test.sh` | Menjalankan suite dengan Firestore emulator. |

Lihat [README](../README.md) untuk konfigurasi aplikasi, detail deployment,
dan troubleshooting.

# Memory Verse Tracker

A Streamlit app for tracking kids' weekly Bible memory verse progress, backed
by Google Cloud Firestore and deployable to Cloud Run.

## Features

- Add and manage kids with optional classes or groups
- Add weekly memory verses
- Record which kids have memorized each verse
- Dashboard with per-kid and overall progress
- Embedded kids ministry Google Calendar
- Download printable PDF reports
- Teacher-only management controls protected by an environment-variable PIN

## Run locally

Install dependencies and authenticate with Google Cloud:

```bash
pip install -r requirements.txt
gcloud auth application-default login
streamlit run app.py
```

The Google account must have access to a Firestore database in the selected
project. Set `GOOGLE_CLOUD_PROJECT` if the project cannot be inferred from
your local credentials. For local development without a Google project, run
the Firestore emulator and set `FIRESTORE_EMULATOR_HOST` before starting
Streamlit.

Set `ESV_API_TOKEN` to enable ESV text previews on the Lesson Reference page:

```bash
export ESV_API_TOKEN=your-esv-api-token
```

The token is used only in server-side requests and is never rendered in the
browser. Verse references show the cached ESV text when hovered; on touch
devices the same preview is available by tapping the reference.

The Calendar page embeds the configured Google Calendar. The calendar must be
shared publicly or with the intended viewers for events to appear in the
embed. Override the default calendar or timezone with `GOOGLE_CALENDAR_ID` and
`CALENDAR_TIMEZONE` if needed.

## Deploy to Cloud Run

Set the project and region:

```bash
export PROJECT_ID=your-gcp-project-id
export REGION=us-central1
gcloud config set project "$PROJECT_ID"
```

Enable APIs and create a Firestore database once:

```bash
gcloud services enable run.googleapis.com \
    artifactregistry.googleapis.com \
    cloudbuild.googleapis.com \
    firestore.googleapis.com

gcloud firestore databases create --location="$REGION"
```

Create a runtime service account with Firestore access:

```bash
gcloud iam service-accounts create verse-tracker
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member="serviceAccount:verse-tracker@$PROJECT_ID.iam.gserviceaccount.com" \
    --role="roles/datastore.user"
```

Create the container repository, build the image, and deploy it:

```bash
gcloud artifacts repositories create verse-tracker \
    --repository-format=docker --location="$REGION"
gcloud builds submit --tag "$REGION-docker.pkg.dev/$PROJECT_ID/verse-tracker/app"
gcloud run deploy verse-tracker \
    --image="$REGION-docker.pkg.dev/$PROJECT_ID/verse-tracker/app" \
    --region="$REGION" \
    --service-account="verse-tracker@$PROJECT_ID.iam.gserviceaccount.com" \
    --set-env-vars="TEACHER_PIN=replace-with-a-secret,GOOGLE_CLOUD_PROJECT=$PROJECT_ID,ESV_API_TOKEN=your-esv-api-token" \
    --allow-unauthenticated
```

Use Secret Manager rather than plain environment variables for the teacher PIN
and ESV token in a production deployment. Retrieve the service URL with:

```bash
gcloud run services describe verse-tracker \
    --region="$REGION" --format='value(status.url)'
```

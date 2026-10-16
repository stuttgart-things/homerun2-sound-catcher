# Sounds from S3

New sounds should not need a new image, a ConfigMap or SSH onto the
LattePanda. With `SOUNDS_S3_BUCKET` set, the catcher keeps a local copy of a
bucket (MinIO, Ceph RGW, AWS S3) and plays its files like the bundled ones.

## How it works

- At startup, and then every `SOUNDS_S3_INTERVAL` (default 5m), the catcher
  lists the objects under `SOUNDS_S3_PREFIX` and downloads what is new or
  changed (compared by ETag). An object deleted from the bucket disappears
  locally on the next run.
- Rules and `POST /play` keep naming sounds by file name
  (`sound: tusch.mp3`). **A file in the bucket takes precedence over a bundled
  clip with the same name**, so uploading `chime.wav` replaces the default
  chime.
- `GET /api/sounds` lists every available sound and where it comes from
  (`s3` or `bundled`).
- If S3 cannot be reached, the files synced last keep playing. `/healthz`
  shows the state under `s3` (last sync, file count, error, skipped
  objects), but S3 errors never make the catcher unhealthy.
- What gets synced: `.wav`, `.mp3`, `.ogg` and `.flac` directly under the
  prefix. Subfolders, other files, objects over 20 MB, and anything beyond
  500 files are skipped and listed under `skipped`.
- The cache lives under `TTS_CACHE_DIR/s3-sounds`, or `SOUNDS_S3_CACHE_DIR`
  if set. In the pod that is the `/tmp` emptyDir, so it is rebuilt on every
  restart.

## Settings

| Variable | Default | Description |
|---|---|---|
| `SOUNDS_S3_BUCKET` | – | Bucket name. Empty: no sync |
| `SOUNDS_S3_ENDPOINT` | – | e.g. `https://minio.example.com`. Empty: AWS |
| `SOUNDS_S3_PREFIX` | – | Folder within the bucket, e.g. `office` |
| `SOUNDS_S3_REGION` | `us-east-1` | MinIO ignores it; AWS needs the bucket's region |
| `SOUNDS_S3_INTERVAL` | `5m` | How often to sync (at least `10s`) |
| `SOUNDS_S3_ANONYMOUS` | `false` | `true`: unsigned requests, for a public bucket |
| `SOUNDS_S3_CACHE_DIR` | `<TTS_CACHE_DIR>/s3-sounds` | Local copy |

Requests use path-style addressing, which MinIO and Ceph need and AWS
accepts.

## Authentication

The catcher only reads, so give it a user that can do nothing else.

Credentials come from the **standard AWS credential chain** (boto3), not from
catcher-specific variables:

1. **Access key, the usual case with MinIO or Ceph:**
   `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY`.
   - In Kubernetes: `config.s3AccessKey` / `config.s3SecretKey` create the
     Secret `homerun2-sound-catcher-s3` with the keys `accessKey` and
     `secretKey`. The pod reads them as `optional`, so External Secrets can
     provide the same Secret instead and the keys never end up in the
     kustomize OCI.
   - On the LattePanda: put them in `~/.config/sound-catcher.env`
     (`chmod 600`).
2. **Without static keys on AWS:** IRSA, Pod Identity or a role. boto3 picks
   these up through `AWS_WEB_IDENTITY_TOKEN_FILE` / `AWS_ROLE_ARN` with no
   catcher configuration.
3. **Public bucket:** `SOUNDS_S3_ANONYMOUS=true`, which sends no credentials.
   Anyone can read it then, which is acceptable for jingles.

**Internal CA** (MinIO behind a company certificate): point `AWS_CA_BUNDLE` at
the CA file. Do not turn off TLS verification.

### Read-only user in MinIO

```bash
mc alias set lab https://minio.example.com ADMIN_KEY ADMIN_SECRET
mc mb lab/homerun-sounds

cat > sound-catcher-read.json <<'EOF'
{
  "Version": "2012-10-17",
  "Statement": [
    { "Effect": "Allow", "Action": ["s3:ListBucket"],
      "Resource": ["arn:aws:s3:::homerun-sounds"] },
    { "Effect": "Allow", "Action": ["s3:GetObject"],
      "Resource": ["arn:aws:s3:::homerun-sounds/*"] }
  ]
}
EOF
mc admin policy create lab sound-catcher-read sound-catcher-read.json
mc admin user add lab sound-catcher "$(openssl rand -hex 20)"   # note the secret
mc admin policy attach lab sound-catcher-read --user sound-catcher
```

To restrict it to one folder, add `"Condition": {"StringLike":
{"s3:prefix": ["office/*"]}}` to the `ListBucket` statement and use
`homerun-sounds/office/*` as the `GetObject` resource.

Uploading new sounds then needs no catcher: whoever has write access runs
`mc cp tusch.mp3 lab/homerun-sounds/office/`, and the file plays after the
next sync.

## Kubernetes

```bash
kcl run kcl/main.k -Y tests/kcl-deploy-profile.yaml \
  -D config.s3Bucket=homerun-sounds \
  -D config.s3Endpoint=https://minio.example.com \
  -D config.s3Prefix=office \
  -D config.s3AccessKey=… -D config.s3SecretKey=…
```

Leave out `s3AccessKey`/`s3SecretKey` when External Secrets provides
`homerun2-sound-catcher-s3`.

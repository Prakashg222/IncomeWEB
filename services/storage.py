from pathlib import Path

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from flask import current_app


def _s3_client():
    config = current_app.config
    return boto3.client(
        "s3",
        region_name=config["S3_REGION"] or None,
        endpoint_url=config["S3_ENDPOINT_URL"],
        aws_access_key_id=config["S3_ACCESS_KEY_ID"] or None,
        aws_secret_access_key=config["S3_SECRET_ACCESS_KEY"] or None,
    )


def save_original(key: str, filename: str, content: bytes):
    bucket = current_app.config["S3_BUCKET"]
    if bucket:
        try:
            _s3_client().put_object(
                Bucket=bucket,
                Key=key,
                Body=content,
                ContentType="text/html; charset=utf-8",
                ContentDisposition=f'attachment; filename="{filename}"',
                ServerSideEncryption="AES256",
            )
        except (BotoCoreError, ClientError) as exc:
            raise RuntimeError("Could not save the report to configured object storage.") from exc
        return
    if current_app.config.get("IS_PRODUCTION"):
        raise RuntimeError("Persistent object storage is required in production.")
    root = Path(current_app.config["UPLOAD_DIR"]).resolve()
    target = (root / key).resolve()
    if root not in target.parents:
        raise RuntimeError("Invalid upload storage key.")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)


def read_original(key: str):
    bucket = current_app.config["S3_BUCKET"]
    if bucket:
        try:
            response = _s3_client().get_object(Bucket=bucket, Key=key)
            return response["Body"].read()
        except (BotoCoreError, ClientError) as exc:
            raise RuntimeError("Could not read the original report from object storage.") from exc
    if current_app.config.get("IS_PRODUCTION"):
        raise RuntimeError("Persistent object storage is required in production.")
    root = Path(current_app.config["UPLOAD_DIR"]).resolve()
    target = (root / key).resolve()
    if root not in target.parents:
        raise RuntimeError("Invalid upload storage key.")
    return target.read_bytes()


def delete_original(key: str):
    bucket = current_app.config["S3_BUCKET"]
    if bucket:
        try:
            _s3_client().delete_object(Bucket=bucket, Key=key)
        except (BotoCoreError, ClientError) as exc:
            raise RuntimeError("Could not delete the original report from object storage.") from exc
        return
    if current_app.config.get("IS_PRODUCTION"):
        raise RuntimeError("Persistent object storage is required in production.")
    root = Path(current_app.config["UPLOAD_DIR"]).resolve()
    target = (root / key).resolve()
    if root not in target.parents:
        raise RuntimeError("Invalid upload storage key.")
    target.unlink(missing_ok=True)

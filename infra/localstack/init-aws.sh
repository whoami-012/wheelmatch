#!/usr/bin/env sh
set -eu

REGION="${AWS_DEFAULT_REGION:-us-east-1}"
BUCKET="${S3_BUCKET:-wheelmatch-media-local}"

awslocal sqs create-queue --region "$REGION" --queue-name wheelmatch-events-dlq >/dev/null
DLQ_ARN="$(awslocal sqs get-queue-attributes \
  --region "$REGION" \
  --queue-url http://sqs."$REGION".localhost.localstack.cloud:4566/000000000000/wheelmatch-events-dlq \
  --attribute-names QueueArn \
  --query 'Attributes.QueueArn' \
  --output text)"
awslocal sqs create-queue \
  --region "$REGION" \
  --queue-name wheelmatch-events \
  --attributes "{\"RedrivePolicy\":\"{\\\"deadLetterTargetArn\\\":\\\"$DLQ_ARN\\\",\\\"maxReceiveCount\\\":5}\",\"VisibilityTimeout\":\"60\"}" \
  >/dev/null
awslocal s3api head-bucket --bucket "$BUCKET" 2>/dev/null \
  || awslocal s3api create-bucket --region "$REGION" --bucket "$BUCKET" >/dev/null
awslocal s3api put-public-access-block \
  --bucket "$BUCKET" \
  --public-access-block-configuration \
  'BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true'

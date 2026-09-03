#!/usr/bin/env bash
set -o errexit

pip install -r requirements.txt

cd frontend
npm install
npm run build

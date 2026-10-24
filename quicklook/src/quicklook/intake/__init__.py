"""Intake adapters. Both call the same pipeline.process_email; only delivery differs.

realtime: a local SMTP server runs the pipeline on each email as it arrives.
batch:    a job scans a Maildir every N minutes.
"""

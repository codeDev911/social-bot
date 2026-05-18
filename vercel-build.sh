#!/bin/bash
# Make the QuickJS binary executable
chmod +x bin/qjs
# Run your prisma generation if needed
prisma db push

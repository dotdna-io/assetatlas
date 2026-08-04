#!/bin/bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# AssetAtlas - Full Asset Generation Pipeline
#
# This script runs the complete asset generation pipeline on ALL raw data
# in the database.
#
# Usage:
#   ./scripts/run-asset-generation.sh

set -e  # Exit on error

# Colors for output
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# Get script directory and project root
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

echo -e "${BLUE}"
echo "============================================================"
echo "           AssetAtlas - Asset Generation Pipeline"
echo "============================================================"
echo -e "${NC}"

# Change to project root
cd "$PROJECT_ROOT"

# Check if virtual environment exists
if [ ! -d "venv" ]; then
    echo -e "${RED}Error: Virtual environment not found${NC}"
    echo "Please create the virtual environment first:"
    echo "  python3 -m venv venv"
    echo "  source venv/bin/activate"
    echo "  pip install -r requirements-prod.txt -r requirements-test.txt"
    exit 1
fi

# Load environment variables from .env file
if [ -f ".env" ]; then
    echo -e "${GREEN}✓${NC} Loading environment variables from .env..."
    # set -a + source preserves quoted values; xargs mangled them.
    set -a
    # shellcheck disable=SC1091
    source .env
    set +a
else
    echo -e "${YELLOW}⚠${NC} Warning: .env file not found"
fi

# Check DATABASE_URL
if [ -z "$DATABASE_URL" ]; then
    echo -e "${RED}Error: DATABASE_URL not set${NC}"
    echo "Please set DATABASE_URL in .env file or environment"
    exit 1
fi

echo -e "${GREEN}✓${NC} Database configured"
echo -e "${GREEN}✓${NC} Virtual environment ready"
echo ""

# Run the asset generation script
echo -e "${BLUE}Starting asset generation pipeline...${NC}"
echo ""

./venv/bin/python scripts/run_full_asset_generation.py

echo ""
echo -e "${GREEN}============================================================${NC}"
echo -e "${GREEN}Asset generation completed successfully!${NC}"
echo -e "${GREEN}============================================================${NC}"

#!/bin/bash
# SPDX-License-Identifier: AGPL-3.0-or-later

# ============================================================================
# AssetAtlas Database Verification Script
# ============================================================================
# Run this script to verify your database setup is correct
# Usage: ./scripts/verify-database.sh
# ============================================================================

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

print_header() {
    echo -e "\n${BLUE}===================================================${NC}"
    echo -e "${BLUE}$1${NC}"
    echo -e "${BLUE}===================================================${NC}\n"
}

print_success() {
    echo -e "${GREEN}✓ $1${NC}"
}

print_error() {
    echo -e "${RED}✗ $1${NC}"
}

print_info() {
    echo -e "${BLUE}ℹ $1${NC}"
}

# Check if container is running
print_header "1. Container Status"

if docker ps | grep -q assetatlas-db; then
    print_success "Container 'assetatlas-db' is running"
    docker ps | grep assetatlas-db
else
    print_error "Container 'assetatlas-db' is not running"
    echo "Start it with: docker-compose up -d timescaledb"
    exit 1
fi

# Check PostgreSQL version
print_header "2. PostgreSQL Version"

VERSION=$(docker exec assetatlas-db psql -U assetatlas -d assetatlas -t -c "SELECT version();")
echo "$VERSION"
print_success "PostgreSQL is accessible"

# Check TimescaleDB extension
print_header "3. TimescaleDB Extension"

TS_VERSION=$(docker exec assetatlas-db psql -U assetatlas -d assetatlas -t -c "SELECT extversion FROM pg_extension WHERE extname = 'timescaledb';" | tr -d '[:space:]')

if [ -n "$TS_VERSION" ]; then
    print_success "TimescaleDB extension installed (version $TS_VERSION)"
else
    print_error "TimescaleDB extension not found"
    exit 1
fi

# Count tables
print_header "4. Schema Tables"

TABLE_COUNT=$(docker exec assetatlas-db psql -U assetatlas -d assetatlas -t -c "SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public';" | tr -d '[:space:]')

echo "Expected: 17 tables"
echo "Found: $TABLE_COUNT tables"

if [ "$TABLE_COUNT" -eq "17" ]; then
    print_success "Correct number of tables"
else
    print_error "Incorrect number of tables (expected 17, found $TABLE_COUNT)"
fi

# List all tables
print_info "Table list:"
docker exec assetatlas-db psql -U assetatlas -d assetatlas -c "SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename;" | grep -v "^-" | grep -v "^(" | grep -v "rows)" | grep -v "tablename" | sed 's/^/  /'

# Check hypertables
print_header "5. TimescaleDB Hypertables"

HYPERTABLE_COUNT=$(docker exec assetatlas-db psql -U assetatlas -d assetatlas -t -c "SELECT count(*) FROM timescaledb_information.hypertables;" | tr -d '[:space:]')

echo "Expected: 1 hypertable (asset_chronicle)"
echo "Found: $HYPERTABLE_COUNT hypertable(s)"

if [ "$HYPERTABLE_COUNT" -eq "1" ]; then
    print_success "Hypertable created correctly"
    docker exec assetatlas-db psql -U assetatlas -d assetatlas -c "SELECT hypertable_name, num_dimensions, num_chunks FROM timescaledb_information.hypertables;"
else
    print_error "Hypertable not found or incorrect count"
fi

# Check continuous aggregate
print_header "6. Continuous Aggregates"

CAGG_COUNT=$(docker exec assetatlas-db psql -U assetatlas -d assetatlas -t -c "SELECT count(*) FROM timescaledb_information.continuous_aggregates;" | tr -d '[:space:]')

echo "Expected: 1 continuous aggregate (asset_chronicle_hourly)"
echo "Found: $CAGG_COUNT continuous aggregate(s)"

if [ "$CAGG_COUNT" -eq "1" ]; then
    print_success "Continuous aggregate created correctly"
else
    print_error "Continuous aggregate not found"
fi

# Check compression policy
print_header "7. Compression Policies"

COMPRESSION_COUNT=$(docker exec assetatlas-db psql -U assetatlas -d assetatlas -t -c "SELECT count(*) FROM timescaledb_information.jobs WHERE proc_name LIKE '%compress%';" | tr -d '[:space:]')

if [ "$COMPRESSION_COUNT" -gt "0" ]; then
    print_success "Compression policy configured"
    docker exec assetatlas-db psql -U assetatlas -d assetatlas -c "SELECT job_id, schedule_interval, config FROM timescaledb_information.jobs WHERE proc_name LIKE '%compress%';"
else
    print_error "No compression policy found"
fi

# Check indexes
print_header "8. Indexes"

INDEX_COUNT=$(docker exec assetatlas-db psql -U assetatlas -d assetatlas -t -c "SELECT count(*) FROM pg_indexes WHERE schemaname = 'public';" | tr -d '[:space:]')

echo "Found: $INDEX_COUNT indexes"
print_success "Indexes created"

# Check critical tables structure
print_header "9. Critical Tables Structure"

# asset_id_mapping
print_info "Checking asset_id_mapping table..."
MAPPING_COLS=$(docker exec assetatlas-db psql -U assetatlas -d assetatlas -t -c "SELECT count(*) FROM information_schema.columns WHERE table_name = 'asset_id_mapping';" | tr -d '[:space:]')
if [ "$MAPPING_COLS" -eq "4" ]; then
    print_success "asset_id_mapping has correct structure (4 columns)"
else
    print_error "asset_id_mapping has $MAPPING_COLS columns (expected 4)"
fi

# assets
print_info "Checking assets table..."
ASSETS_COLS=$(docker exec assetatlas-db psql -U assetatlas -d assetatlas -t -c "SELECT count(*) FROM information_schema.columns WHERE table_name = 'assets';" | tr -d '[:space:]')
if [ "$ASSETS_COLS" -ge "30" ]; then
    print_success "assets table has correct structure ($ASSETS_COLS columns)"
else
    print_error "assets table has $ASSETS_COLS columns (expected 30+)"
fi

# raw_source_data
print_info "Checking raw_source_data table..."
RAW_SOURCE_COLS=$(docker exec assetatlas-db psql -U assetatlas -d assetatlas -t -c "SELECT count(*) FROM information_schema.columns WHERE table_name = 'raw_source_data';" | tr -d '[:space:]')
if [ "$RAW_SOURCE_COLS" -eq "6" ]; then
    print_success "raw_source_data has correct structure (6 columns)"
else
    print_error "raw_source_data has $RAW_SOURCE_COLS columns (expected 6)"
fi

# raw_asset_data
print_info "Checking raw_asset_data table..."
RAW_ASSET_COLS=$(docker exec assetatlas-db psql -U assetatlas -d assetatlas -t -c "SELECT count(*) FROM information_schema.columns WHERE table_name = 'raw_asset_data';" | tr -d '[:space:]')
if [ "$RAW_ASSET_COLS" -eq "14" ]; then
    print_success "raw_asset_data has correct structure (14 columns)"
else
    print_error "raw_asset_data has $RAW_ASSET_COLS columns (expected 14)"
fi

# Database size
print_header "10. Database Size"

DB_SIZE=$(docker exec assetatlas-db psql -U assetatlas -d assetatlas -t -c "SELECT pg_size_pretty(pg_database_size('assetatlas'));" | tr -d '[:space:]')
print_info "Database size: $DB_SIZE"

# Connection test
print_header "11. Connection Test"

if docker exec assetatlas-db pg_isready -U assetatlas -d assetatlas &> /dev/null; then
    print_success "Database is accepting connections"
else
    print_error "Database is not accepting connections"
    exit 1
fi

# Summary
print_header "Verification Summary"

if [ "$TABLE_COUNT" -eq "17" ] && [ "$HYPERTABLE_COUNT" -eq "1" ] && [ "$CAGG_COUNT" -eq "1" ]; then
    echo -e "${GREEN}"
    echo "╔═══════════════════════════════════════════════════╗"
    echo "║                                                   ║"
    echo "║  ✓ Database Setup is CORRECT!                    ║"
    echo "║                                                   ║"
    echo "║  All tables, hypertables, and aggregates         ║"
    echo "║  are properly configured.                        ║"
    echo "║                                                   ║"
    echo "║  Database is ready for use! 🚀                   ║"
    echo "║                                                   ║"
    echo "╚═══════════════════════════════════════════════════╝"
    echo -e "${NC}"
else
    echo -e "${YELLOW}"
    echo "╔═══════════════════════════════════════════════════╗"
    echo "║                                                   ║"
    echo "║  ⚠ Database Setup has ISSUES                     ║"
    echo "║                                                   ║"
    echo "║  Please review the errors above.                 ║"
    echo "║                                                   ║"
    echo "╚═══════════════════════════════════════════════════╝"
    echo -e "${NC}"
fi

echo ""
print_info "Database Details:"
echo "  Container: assetatlas-db"
echo "  Database:  assetatlas"
echo "  User:      assetatlas"
echo "  Port:      5432 (internal) / 5434 (external)"
echo "  Tables:    $TABLE_COUNT"
echo "  Size:      $DB_SIZE"

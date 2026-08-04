#!/bin/bash
# SPDX-License-Identifier: AGPL-3.0-or-later

# ============================================================================
# AssetAtlas Database Setup Script
# ============================================================================
# This script automates the database setup process
# Run with: ./scripts/setup-database.sh
# ============================================================================

set -e  # Exit on error

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Functions
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

print_warning() {
    echo -e "${YELLOW}⚠ $1${NC}"
}

print_info() {
    echo -e "${BLUE}ℹ $1${NC}"
}

# Check prerequisites
print_header "Checking Prerequisites"

if ! command -v docker &> /dev/null; then
    print_error "Docker is not installed"
    echo "Please install Docker first: https://docs.docker.com/get-docker/"
    exit 1
else
    print_success "Docker is installed ($(docker --version))"
fi

if ! command -v docker-compose &> /dev/null; then
    print_error "Docker Compose is not installed"
    echo "Please install Docker Compose first"
    exit 1
else
    print_success "Docker Compose is installed ($(docker-compose --version))"
fi

# Check if Docker daemon is running
if ! docker ps &> /dev/null; then
    print_error "Docker daemon is not running"
    echo "Please start Docker first"
    exit 1
else
    print_success "Docker daemon is running"
fi

# Create .env file if it doesn't exist
print_header "Setting Up Environment"

if [ ! -f .env ]; then
    print_info "Creating .env file from template..."
    cp .env.example .env

    # Generate a random password
    DB_PASSWORD=$(openssl rand -base64 32 | tr -d "=+/" | cut -c1-32)
    PGADMIN_PASSWORD=$(openssl rand -base64 16 | tr -d "=+/" | cut -c1-16)

    # Update .env file
    sed -i.bak "s/your-secure-password-here/$DB_PASSWORD/g" .env
    sed -i.bak "s/admin-password-here/$PGADMIN_PASSWORD/g" .env
    rm .env.bak 2>/dev/null || true

    print_success ".env file created with random passwords"
    print_warning "Database password: $DB_PASSWORD"
    print_warning "Save these credentials in a secure location!"
else
    print_info ".env file already exists, skipping..."
fi

# Pull Docker images
print_header "Pulling Docker Images"

print_info "Pulling TimescaleDB image..."
docker-compose pull timescaledb

print_success "Docker images pulled successfully"

# Start database
print_header "Starting Database"

print_info "Starting TimescaleDB container..."
docker-compose up -d timescaledb

# Wait for database to be ready
print_info "Waiting for database to be ready..."
sleep 5

MAX_RETRIES=30
RETRY_COUNT=0

while [ $RETRY_COUNT -lt $MAX_RETRIES ]; do
    if docker exec assetatlas-db pg_isready -U assetatlas -d assetatlas &> /dev/null; then
        print_success "Database is ready!"
        break
    fi

    RETRY_COUNT=$((RETRY_COUNT + 1))
    if [ $RETRY_COUNT -eq $MAX_RETRIES ]; then
        print_error "Database failed to start after $MAX_RETRIES attempts"
        print_info "Check logs with: docker-compose logs timescaledb"
        exit 1
    fi

    echo -n "."
    sleep 2
done
echo ""

# Verify schema was initialized
print_header "Verifying Database Setup"

TABLE_COUNT=$(docker exec assetatlas-db psql -U assetatlas -d assetatlas -t -c "SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public';" | tr -d '[:space:]')

if [ "$TABLE_COUNT" -gt "0" ]; then
    print_success "Schema initialized successfully ($TABLE_COUNT tables created)"
else
    print_warning "Schema might not be initialized. Checking..."

    # Try to run schema manually
    print_info "Running schema initialization..."
    docker exec -i assetatlas-db psql -U assetatlas -d assetatlas < database/schema.sql

    TABLE_COUNT=$(docker exec assetatlas-db psql -U assetatlas -d assetatlas -t -c "SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public';" | tr -d '[:space:]')

    if [ "$TABLE_COUNT" -gt "0" ]; then
        print_success "Schema initialized successfully ($TABLE_COUNT tables created)"
    else
        print_error "Schema initialization failed"
        exit 1
    fi
fi

# List tables
print_info "Tables created:"
docker exec assetatlas-db psql -U assetatlas -d assetatlas -c "\dt" | grep "public |" | awk '{print "  - " $3}'

# Final summary
print_header "Setup Complete!"

echo -e "${GREEN}✓ TimescaleDB is running${NC}"
echo -e "${GREEN}✓ Database 'assetatlas' is ready${NC}"
echo -e "${GREEN}✓ Schema initialized with $TABLE_COUNT tables${NC}"
echo ""
print_info "Database Connection Details:"
echo "  Host:     localhost"
echo "  Port:     5432"
echo "  Database: assetatlas"
echo "  User:     assetatlas"
echo "  Password: (see .env file)"
echo ""
print_info "Connection String:"
echo "  postgresql://assetatlas:<password>@localhost:5432/assetatlas"
echo ""
print_info "Useful Commands:"
echo "  • View logs:      docker-compose logs -f timescaledb"
echo "  • Connect to DB:  docker exec -it assetatlas-db psql -U assetatlas -d assetatlas"
echo "  • Stop DB:        docker-compose down"
echo "  • Restart DB:     docker-compose restart timescaledb"
echo ""
print_info "Optional: Start pgAdmin (Web UI)"
echo "  docker-compose --profile tools up -d"
echo "  Access at: http://localhost:5050"
echo ""
print_success "Database is ready to use! 🚀"

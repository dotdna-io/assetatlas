# AssetAtlas Database - Docker Deployment Guide

## 🚀 Quick Start

### 1. Prerequisites

```bash
# Make sure you have Docker and Docker Compose installed
docker --version
docker-compose --version

# On Ubuntu/Debian:
sudo apt update
sudo apt install docker.io docker-compose -y
sudo systemctl start docker
sudo systemctl enable docker
```

### 2. Setup Environment

```bash
# Navigate to the project directory
cd /path/to/assetatlas_backend

# Create .env file from example
cp .env.example .env

# Edit .env and set a secure password
nano .env
# Change DB_PASSWORD to something secure!
```

### 3. Start the Database

```bash
# Start TimescaleDB (database only)
docker-compose up -d timescaledb

# Check if it's running
docker-compose ps

# View logs
docker-compose logs -f timescaledb
```

**That's it!** The schema is automatically initialized on first startup.

---

## 📊 Verify Database is Working

```bash
# Connect to the database
docker exec -it assetatlas-db psql -U assetatlas -d assetatlas

# Once connected, run:
\dt                    # List all tables
SELECT count(*) FROM integrations;  # Should return 0 (empty table)
\q                     # Quit
```

---

## 🛠️ Common Operations

### Start/Stop Database

```bash
# Start
docker-compose up -d timescaledb

# Stop
docker-compose down

# Restart
docker-compose restart timescaledb

# Stop and remove data (⚠️ DESTRUCTIVE)
docker-compose down -v
```

### View Logs

```bash
# Real-time logs
docker-compose logs -f timescaledb

# Last 100 lines
docker-compose logs --tail=100 timescaledb
```

### Access Database CLI

```bash
# Using docker exec
docker exec -it assetatlas-db psql -U assetatlas -d assetatlas

# Using psql from host (if installed)
psql -h localhost -U assetatlas -d assetatlas
# Password is in .env file
```

### Backup Database

```bash
# Backup to file
docker exec assetatlas-db pg_dump -U assetatlas assetatlas > backup_$(date +%Y%m%d_%H%M%S).sql

# Backup with compression
docker exec assetatlas-db pg_dump -U assetatlas assetatlas | gzip > backup_$(date +%Y%m%d_%H%M%S).sql.gz
```

### Restore Database

```bash
# From uncompressed backup
docker exec -i assetatlas-db psql -U assetatlas -d assetatlas < backup.sql

# From compressed backup
gunzip -c backup.sql.gz | docker exec -i assetatlas-db psql -U assetatlas -d assetatlas
```

---

## 🎨 Optional: pgAdmin Web UI

pgAdmin provides a web interface for managing your database.

### Start pgAdmin

```bash
# Start pgAdmin (along with database)
docker-compose --profile tools up -d

# pgAdmin will be available at: http://localhost:5050
# Login credentials are in .env file (PGADMIN_EMAIL / PGADMIN_PASSWORD)
```

### Add Server in pgAdmin

1. Open http://localhost:5050
2. Login with credentials from `.env`
3. Right-click "Servers" → "Register" → "Server"
4. **General Tab**: Name = "AssetAtlas"
5. **Connection Tab**:
   - Host: `timescaledb` (container name)
   - Port: `5432`
   - Username: `assetatlas`
   - Password: (from .env DB_PASSWORD)
6. Click "Save"

---

## 🔧 Configuration

### Database Tuning

The default configuration works for most setups, but you can customize:

```bash
# Uncomment the postgresql.conf volume mount in docker-compose.yml
# Edit database/postgresql.conf
# Restart the container
docker-compose restart timescaledb
```

### Change Password

```bash
# 1. Update .env file with new password

# 2. Connect to database
docker exec -it assetatlas-db psql -U assetatlas -d assetatlas

# 3. Change password
ALTER USER assetatlas WITH PASSWORD 'new-secure-password';
\q

# 4. Restart container
docker-compose restart timescaledb
```

### Resource Limits

Edit `docker-compose.yml` to adjust CPU/memory:

```yaml
deploy:
  resources:
    limits:
      cpus: '4.0'      # Increase for more CPU
      memory: 8G       # Increase for more RAM
```

---

## 📦 Data Persistence

Database data is stored in Docker volumes:

```bash
# List volumes
docker volume ls | grep assetatlas

# Inspect volume
docker volume inspect assetatlas_backend_timescaledb-data

# Volume location (usually):
/var/lib/docker/volumes/assetatlas_backend_timescaledb-data/_data
```

**⚠️ Important**: Even if you remove containers, volumes persist. To delete data:

```bash
docker-compose down -v  # Removes volumes too
```

---

## 🔍 Troubleshooting

### Container Won't Start

```bash
# Check logs for errors
docker-compose logs timescaledb

# Common issues:
# 1. Port 5432 already in use
#    Solution: Change port in docker-compose.yml (e.g., "5433:5432")
# 2. Permission errors
#    Solution: sudo chown -R 999:999 /var/lib/docker/volumes/...
```

### Can't Connect from Application

```bash
# 1. Check container is running
docker-compose ps

# 2. Test connection
docker exec assetatlas-db pg_isready -U assetatlas

# 3. Check network
docker network ls
docker network inspect assetatlas_backend_assetatlas-network

# 4. From Python/your app:
# Connection string: postgresql://assetatlas:PASSWORD@localhost:5432/assetatlas
```

### Schema Not Initialized

```bash
# Manually run schema
docker exec -i assetatlas-db psql -U assetatlas -d assetatlas < database/schema.sql

# Or copy and run inside container
docker cp database/schema.sql assetatlas-db:/tmp/
docker exec -it assetatlas-db psql -U assetatlas -d assetatlas -f /tmp/schema.sql
```

### Database is Slow

```bash
# Check resource usage
docker stats assetatlas-db

# Check query performance
docker exec -it assetatlas-db psql -U assetatlas -d assetatlas
SELECT * FROM pg_stat_activity;

# Enable slow query logging (in postgresql.conf)
log_min_duration_statement = 1000  # Log queries > 1 second
```

---

## 🔒 Security Best Practices

### 1. Use Strong Passwords
```bash
# Generate secure password
openssl rand -base64 32

# Update .env file
DB_PASSWORD=<generated-password>
```

### 2. Don't Expose Database Publicly
```yaml
# In docker-compose.yml, bind to localhost only:
ports:
  - "127.0.0.1:5432:5432"  # Only accessible from localhost
```

### 3. Regular Backups
```bash
# Set up cron job for daily backups
crontab -e

# Add this line (backs up at 2 AM daily):
0 2 * * * cd /path/to/assetatlas_backend && docker exec assetatlas-db pg_dump -U assetatlas assetatlas | gzip > /backups/assetatlas_$(date +\%Y\%m\%d).sql.gz
```

### 4. Update Regularly
```bash
# Pull latest TimescaleDB image
docker-compose pull timescaledb

# Recreate container
docker-compose up -d timescaledb
```

---

## 📈 Monitoring

### Database Size

```sql
-- Connect to database and run:
SELECT
    pg_size_pretty(pg_database_size('assetatlas')) as total_size;

-- Table sizes
SELECT
    schemaname,
    tablename,
    pg_size_pretty(pg_total_relation_size(schemaname||'.'||tablename)) AS size
FROM pg_tables
WHERE schemaname = 'public'
ORDER BY pg_total_relation_size(schemaname||'.'||tablename) DESC;
```

### Connection Count

```sql
SELECT count(*) FROM pg_stat_activity WHERE datname = 'assetatlas';
```

### TimescaleDB Health

```sql
-- Check compression
SELECT * FROM timescaledb_information.chunks;

-- Check background jobs
SELECT * FROM timescaledb_information.jobs;
```

---

## 🆘 Need Help?

- TimescaleDB Docs: https://docs.timescale.com/
- PostgreSQL Docs: https://www.postgresql.org/docs/
- Docker Docs: https://docs.docker.com/

---

## 📝 Next Steps

Once your database is running:

1. ✅ Test connection from your application
2. ✅ Set up regular backups
3. ✅ Configure monitoring
4. ✅ Build your data connectors
5. ✅ Run asset generation pipeline

See `schema.sql` and the migrations in `migrations/` for database structure details.

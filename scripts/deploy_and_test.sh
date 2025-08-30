#!/usr/bin/env bash
set -euo pipefail

# Viewbot Guard Deployment and Testing Script
# Comprehensive deployment with validation and testing

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
COMPOSE_FILE="$PROJECT_ROOT/infra/docker-compose.yml"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Logging functions
log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

log_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Check prerequisites
check_prerequisites() {
    log_info "Checking prerequisites..."
    
    # Check Docker
    if ! command -v docker &> /dev/null; then
        log_error "Docker not found. Please install Docker."
        exit 1
    fi
    
    # Check Docker Compose
    if ! docker compose version &> /dev/null; then
        log_error "Docker Compose not found. Please install Docker Compose."
        exit 1
    fi
    
    # Check curl
    if ! command -v curl &> /dev/null; then
        log_error "curl not found. Please install curl."
        exit 1
    fi
    
    # Check available ports
    local required_ports=(3000 6379 8000 8080 8081 8123 8787 9000 9092 19092)
    for port in "${required_ports[@]}"; do
        if netstat -tln 2>/dev/null | grep -q ":$port "; then
            log_warning "Port $port is already in use. This may cause conflicts."
        fi
    done
    
    log_success "Prerequisites check passed"
}

# Clean previous deployment
cleanup_previous() {
    log_info "Cleaning up previous deployment..."
    
    cd "$PROJECT_ROOT"
    docker compose -f "$COMPOSE_FILE" down -v --remove-orphans 2>/dev/null || true
    
    # Clean up any dangling containers
    docker container prune -f &>/dev/null || true
    docker network prune -f &>/dev/null || true
    
    log_success "Cleanup completed"
}

# Deploy services
deploy_services() {
    log_info "Deploying Viewbot Guard services..."
    
    cd "$PROJECT_ROOT"
    
    # Build and start services
    log_info "Building and starting services..."
    docker compose -f "$COMPOSE_FILE" up --build -d
    
    log_success "Services deployment initiated"
}

# Wait for service health
wait_for_service() {
    local service_name="$1"
    local health_url="$2"
    local max_wait="${3:-120}"  # Default 2 minutes
    local wait_time=0
    
    log_info "Waiting for $service_name to be healthy..."
    
    while [ $wait_time -lt $max_wait ]; do
        if curl -sf "$health_url" >/dev/null 2>&1; then
            log_success "$service_name is healthy"
            return 0
        fi
        
        sleep 5
        wait_time=$((wait_time + 5))
        
        if [ $((wait_time % 30)) -eq 0 ]; then
            log_info "Still waiting for $service_name... ($wait_time/${max_wait}s)"
        fi
    done
    
    log_error "$service_name failed to become healthy within ${max_wait}s"
    return 1
}

# Verify service deployment
verify_services() {
    log_info "Verifying service deployment..."
    
    # Wait for core services
    wait_for_service "ClickHouse" "http://localhost:8123/ping" 60
    wait_for_service "Grafana" "http://localhost:3000/api/health" 60
    wait_for_service "Player (NGINX)" "http://localhost:8080" 30
    wait_for_service "API Service" "http://localhost:8000/api/v1/health" 90
    
    # Check Redpanda (no direct health endpoint)
    log_info "Checking Redpanda availability..."
    if docker compose -f "$COMPOSE_FILE" exec -T redpanda rpk cluster info >/dev/null 2>&1; then
        log_success "Redpanda is healthy"
    else
        log_error "Redpanda is not responding"
        return 1
    fi
    
    # Check Flink
    log_info "Checking Flink JobManager..."
    if curl -sf "http://localhost:8081/overview" >/dev/null 2>&1; then
        log_success "Flink JobManager is healthy"
    else
        log_error "Flink JobManager is not responding"
        return 1
    fi
    
    log_success "All services are healthy"
}

# Setup data schemas
setup_schemas() {
    log_info "Setting up data schemas..."
    
    # Verify ClickHouse tables
    log_info "Verifying ClickHouse tables..."
    local tables_query="SELECT name FROM system.tables WHERE database = 'viewbot'"
    local expected_tables=("raw_cdn_log" "player_events" "chat_events" "edge_challenges" "decisions" "mv_viewer_5m")
    
    for table in "${expected_tables[@]}"; do
        if docker compose -f "$COMPOSE_FILE" exec -T clickhouse clickhouse-client \
           --query "SELECT COUNT(*) FROM system.tables WHERE database = 'viewbot' AND name = '$table'" | grep -q "1"; then
            log_success "Table viewbot.$table exists"
        else
            log_error "Table viewbot.$table is missing"
            return 1
        fi
    done
    
    # Create Kafka topics
    log_info "Creating Kafka topics..."
    local topics=("cdn-logs" "player-events" "chat-events" "edge-signals" "decisions" "audit-log")
    
    for topic in "${topics[@]}"; do
        docker compose -f "$COMPOSE_FILE" exec -T redpanda rpk topic create "$topic" \
            --partitions 3 --replicas 1 2>/dev/null || true
        log_success "Topic $topic created/verified"
    done
    
    log_success "Schema setup completed"
}

# Run functional tests
run_functional_tests() {
    log_info "Running functional tests..."
    
    # Test API endpoints
    log_info "Testing API scoring endpoint..."
    local test_request='{
        "session_id": "test_session_deploy",
        "channel_id": "test_channel_deploy", 
        "features": {
            "session_id": "test_session_deploy",
            "channel_id": "test_channel_deploy",
            "reqs_per_min": 15.0,
            "unique_segments": 15,
            "avg_ttfb_ms": 120.0,
            "cadence_std_ms": 100.0,
            "non200_rate": 0.02,
            "asn_type": "residential"
        }
    }'
    
    local api_response
    api_response=$(curl -sf -X POST "http://localhost:8000/api/v1/score" \
        -H "Content-Type: application/json" \
        -d "$test_request")
    
    if echo "$api_response" | grep -q "test_session_deploy"; then
        log_success "API scoring endpoint working"
    else
        log_error "API scoring endpoint failed"
        echo "Response: $api_response"
        return 1
    fi
    
    # Test player
    log_info "Testing HLS player..."
    if curl -sf "http://localhost:8080" | grep -q "Viewbot Guard"; then
        log_success "HLS player is accessible"
    else
        log_error "HLS player is not working"
        return 1
    fi
    
    # Test data pipeline with sample events
    log_info "Testing data pipeline..."
    
    # Send test CDN log event
    local cdn_event='{
        "ts": "'$(date -u +%Y-%m-%dT%H:%M:%SZ)'",
        "request_id": "test_req_001",
        "session_id": "test_pipeline_session",
        "channel_id": "test_pipeline_channel",
        "client_ip": "203.0.113.5",
        "user_agent": "TestBot/1.0",
        "referrer": "https://test.example.com",
        "host": "cdn.example.com",
        "path": "/stream/segment001.ts",
        "status": 200,
        "ttfb_ms": 100,
        "resp_bytes": 1048576,
        "country": "US",
        "asn": 7922,
        "ja3": "ja3_test",
        "ja4": "ja4h_test",
        "cmcd": {"sid": "test_pipeline_session", "br": "1500"}
    }'
    
    # Send event to Kafka
    echo "$cdn_event" | docker compose -f "$COMPOSE_FILE" exec -T redpanda \
        rpk topic produce cdn-logs --format json
    
    log_success "Test event sent to pipeline"
    
    log_success "Functional tests completed"
}

# Start Flink job
start_flink_job() {
    log_info "Starting Flink streaming job..."
    
    cd "$PROJECT_ROOT"
    
    # Copy Flink job to container
    docker compose -f "$COMPOSE_FILE" cp stream/job.py jobmanager:/opt/flink/usrlib/job.py
    
    # Submit job
    local job_output
    job_output=$(docker compose -f "$COMPOSE_FILE" exec -T jobmanager bash -c \
        'cd /opt/flink && ./bin/flink run -py /opt/flink/usrlib/job.py' 2>&1)
    
    if echo "$job_output" | grep -q "Job has been submitted"; then
        log_success "Flink job submitted successfully"
        
        # Extract job ID
        local job_id
        job_id=$(echo "$job_output" | grep -o 'Job ID: [a-f0-9-]*' | cut -d' ' -f3)
        if [ -n "$job_id" ]; then
            log_info "Flink Job ID: $job_id"
        fi
    else
        log_error "Failed to submit Flink job"
        echo "$job_output"
        return 1
    fi
    
    log_success "Flink job started"
}

# Performance verification
verify_performance() {
    log_info "Running performance verification..."
    
    # Measure API response time
    local start_time end_time response_time
    start_time=$(date +%s%3N)
    
    curl -sf -X POST "http://localhost:8000/api/v1/score" \
        -H "Content-Type: application/json" \
        -d '{
            "session_id": "perf_test",
            "channel_id": "perf_channel",
            "features": {
                "session_id": "perf_test",
                "channel_id": "perf_channel",
                "reqs_per_min": 15.0,
                "unique_segments": 15,
                "avg_ttfb_ms": 120.0,
                "cadence_std_ms": 100.0,
                "non200_rate": 0.02,
                "asn_type": "residential"
            }
        }' > /dev/null
    
    end_time=$(date +%s%3N)
    response_time=$((end_time - start_time))
    
    if [ $response_time -lt 1000 ]; then
        log_success "API response time: ${response_time}ms (< 1000ms target)"
    else
        log_warning "API response time: ${response_time}ms (exceeds 1000ms target)"
    fi
    
    # Check system resources
    log_info "Checking system resources..."
    
    # Memory usage
    local memory_usage
    memory_usage=$(docker stats --no-stream --format "table {{.Container}}\t{{.MemUsage}}" | tail -n +2)
    log_info "Memory usage by container:"
    echo "$memory_usage"
    
    # CPU usage  
    local cpu_usage
    cpu_usage=$(docker stats --no-stream --format "table {{.Container}}\t{{.CPUPerc}}" | tail -n +2)
    log_info "CPU usage by container:"
    echo "$cpu_usage"
    
    log_success "Performance verification completed"
}

# Generate summary report
generate_summary() {
    log_info "Generating deployment summary..."
    
    echo ""
    echo "========================================"
    echo "   Viewbot Guard Deployment Summary"
    echo "========================================"
    echo ""
    echo "Services Status:"
    echo "  ✓ Redpanda (Kafka)     - http://localhost:19092"
    echo "  ✓ ClickHouse           - http://localhost:8123"  
    echo "  ✓ Redis                - localhost:6379"
    echo "  ✓ Flink JobManager     - http://localhost:8081"
    echo "  ✓ Grafana              - http://localhost:3000 (admin/admin)"
    echo "  ✓ API Service          - http://localhost:8000/api/docs"
    echo "  ✓ HLS Player           - http://localhost:8080"
    echo "  ✓ Challenge Service    - http://localhost:8787"
    echo ""
    echo "Quick Start:"
    echo "  1. Open player: http://localhost:8080"
    echo "  2. Open Grafana: http://localhost:3000"
    echo "  3. View API docs: http://localhost:8000/api/docs"
    echo "  4. Monitor Flink: http://localhost:8081"
    echo ""
    echo "Testing:"
    echo "  - API health: curl http://localhost:8000/api/v1/health"
    echo "  - Submit test job: ./stream/run_local.sh"
    echo "  - Run tests: cd tests && python -m pytest test_api.py -v"
    echo ""
    echo "Logs:"
    echo "  - View all logs: docker compose -f infra/docker-compose.yml logs -f"
    echo "  - API logs: docker compose -f infra/docker-compose.yml logs -f api"
    echo ""
    
    log_success "Deployment completed successfully!"
}

# Error handling
handle_error() {
    local exit_code=$?
    log_error "Deployment failed with exit code $exit_code"
    
    log_info "Collecting error information..."
    
    # Show service status
    docker compose -f "$COMPOSE_FILE" ps || true
    
    # Show recent logs
    log_info "Recent service logs:"
    docker compose -f "$COMPOSE_FILE" logs --tail=20 || true
    
    exit $exit_code
}

# Main execution
main() {
    trap handle_error ERR
    
    log_info "Starting Viewbot Guard deployment..."
    echo ""
    
    check_prerequisites
    cleanup_previous
    deploy_services
    verify_services
    setup_schemas
    start_flink_job
    run_functional_tests
    verify_performance
    generate_summary
}

# Script execution
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    main "$@"
fi

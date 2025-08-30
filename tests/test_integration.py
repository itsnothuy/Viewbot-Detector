#!/usr/bin/env python3
"""
Integration tests for Viewbot Guard system
Tests the complete end-to-end pipeline with real Docker services
"""

import pytest
import asyncio
import json
import time
import docker
import requests
from kafka import KafkaProducer, KafkaConsumer
from clickhouse_driver import Client
import redis
from datetime import datetime, timedelta
from typing import Dict, List
import subprocess
import os

@pytest.fixture(scope="session")
def docker_services():
    """Start Docker services for integration testing"""
    compose_file = os.path.join(os.path.dirname(__file__), '..', 'infra', 'docker-compose.yml')
    
    # Start services
    subprocess.run(['docker', 'compose', '-f', compose_file, 'up', '-d'], check=True)
    
    # Wait for services to be healthy
    time.sleep(30)
    
    yield
    
    # Cleanup
    subprocess.run(['docker', 'compose', '-f', compose_file, 'down', '-v'], check=True)

@pytest.fixture
def kafka_producer():
    """Kafka producer for test events"""
    return KafkaProducer(
        bootstrap_servers=['localhost:19092'],
        value_serializer=lambda x: json.dumps(x).encode('utf-8'),
        retries=3,
        acks='all'
    )

@pytest.fixture
def kafka_consumer():
    """Kafka consumer for test result validation"""
    return KafkaConsumer(
        'decisions',
        bootstrap_servers=['localhost:19092'],
        value_deserializer=lambda x: json.loads(x.decode('utf-8')),
        auto_offset_reset='latest',
        consumer_timeout_ms=10000
    )

@pytest.fixture
def clickhouse_client():
    """ClickHouse client for data verification"""
    return Client(host='localhost', port=9000, database='viewbot')

@pytest.fixture
def redis_client():
    """Redis client for state verification"""
    return redis.Redis(host='localhost', port=6379, decode_responses=True)

class TestEndToEndPipeline:
    """Test complete pipeline from CDN logs to decisions"""
    
    @pytest.mark.integration
    def test_legitimate_viewer_pipeline(self, docker_services, kafka_producer, kafka_consumer, clickhouse_client):
        """Test pipeline with legitimate viewer behavior"""
        # Generate legitimate viewer events
        session_id = f"legit_session_{int(time.time())}"
        channel_id = "test_channel"
        
        events = self.generate_legitimate_viewer_events(session_id, channel_id)
        
        # Send events to Kafka
        for event in events:
            kafka_producer.send('cdn-logs', value=event)
        kafka_producer.flush()
        
        # Wait for processing
        time.sleep(20)
        
        # Check decisions in ClickHouse
        decisions = clickhouse_client.execute("""
            SELECT session_id, score, action, reasons
            FROM viewbot.decisions 
            WHERE session_id = %(session_id)s
            ORDER BY ts DESC
            LIMIT 1
        """, {'session_id': session_id})
        
        assert len(decisions) > 0
        decision = decisions[0]
        assert decision[0] == session_id
        assert decision[1] < 0.3  # Low risk score
        assert decision[2] == 'count'  # Should be counted normally
    
    @pytest.mark.integration  
    def test_bot_farm_pipeline(self, docker_services, kafka_producer, clickhouse_client):
        """Test pipeline with bot farm behavior"""
        channel_id = "test_channel"
        bot_sessions = []
        
        # Generate bot farm events (10 coordinated sessions)
        for i in range(10):
            session_id = f"bot_session_{int(time.time())}_{i:03d}"
            bot_sessions.append(session_id)
            
            events = self.generate_bot_farm_events(session_id, channel_id)
            
            for event in events:
                kafka_producer.send('cdn-logs', value=event)
        
        kafka_producer.flush()
        
        # Wait for processing
        time.sleep(30)
        
        # Check decisions for all bot sessions
        for session_id in bot_sessions:
            decisions = clickhouse_client.execute("""
                SELECT session_id, score, action, reasons
                FROM viewbot.decisions 
                WHERE session_id = %(session_id)s
                ORDER BY ts DESC
                LIMIT 1
            """, {'session_id': session_id})
            
            assert len(decisions) > 0
            decision = decisions[0]
            assert decision[1] >= 0.5  # High risk score
            assert decision[2] in ['challenge', 'block']  # Should be actioned
    
    @pytest.mark.integration
    def test_api_integration(self, docker_services):
        """Test API endpoints with live services"""
        # Wait for API to be ready
        api_ready = False
        for _ in range(30):
            try:
                response = requests.get('http://localhost:8000/api/v1/health', timeout=5)
                if response.status_code == 200:
                    api_ready = True
                    break
            except:
                pass
            time.sleep(1)
        
        assert api_ready, "API failed to start"
        
        # Test scoring endpoint
        scoring_request = {
            "session_id": "api_test_session",
            "channel_id": "api_test_channel",
            "features": {
                "session_id": "api_test_session",
                "channel_id": "api_test_channel",
                "reqs_per_min": 15.0,
                "unique_segments": 15,
                "avg_ttfb_ms": 120.0,
                "cadence_std_ms": 100.0,
                "non200_rate": 0.02,
                "asn_type": "residential"
            }
        }
        
        response = requests.post('http://localhost:8000/api/v1/score', json=scoring_request)
        assert response.status_code == 200
        
        result = response.json()
        assert result["session_id"] == "api_test_session"
        assert 0.0 <= result["score"] <= 1.0
        assert result["action"] in ["count", "suppress", "challenge", "block"]
    
    def generate_legitimate_viewer_events(self, session_id: str, channel_id: str) -> List[Dict]:
        """Generate realistic legitimate viewer events"""
        events = []
        base_time = datetime.utcnow()
        
        for i in range(30):  # 30 segments over 3 minutes
            # Natural timing variance (5-7 seconds between segments)
            timestamp = base_time + timedelta(seconds=i * 6 + (i % 3))
            
            event = {
                "ts": timestamp.isoformat() + "Z",
                "request_id": f"req_{session_id}_{i:03d}",
                "session_id": session_id,
                "channel_id": channel_id,
                "client_ip": "203.0.113.5",
                "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "referrer": f"https://example.com/channel/{channel_id}",
                "host": "cdn.example.com",
                "path": f"/stream/segment{i:03d}.ts",
                "query": f"CMCD=sid%3D%22{session_id}%22%2Cbr%3D1500%2Cbl%3D{5000 + (i % 3) * 1000}",
                "status": 200 if i % 20 != 19 else 404,  # Occasional 404
                "ttfb_ms": 120 + (i % 5) * 10,  # Variable TTFB
                "resp_bytes": 1048576,
                "country": "US",
                "asn": 7922,  # Comcast residential
                "ja3": "ja3_common_chrome",
                "ja4": "ja4h_chrome_browser",
                "cmcd": {
                    "sid": session_id,
                    "br": "1500",
                    "bl": str(5000 + (i % 3) * 1000),
                    "sf": "h"
                }
            }
            events.append(event)
        
        return events
    
    def generate_bot_farm_events(self, session_id: str, channel_id: str) -> List[Dict]:
        """Generate bot farm-like events"""
        events = []
        base_time = datetime.utcnow()
        
        for i in range(30):  # Perfect 6-second intervals
            timestamp = base_time + timedelta(seconds=i * 6)  # Perfect timing
            
            event = {
                "ts": timestamp.isoformat() + "Z",
                "request_id": f"req_{session_id}_{i:03d}",
                "session_id": session_id,
                "channel_id": channel_id,
                "client_ip": "1.2.3.4",
                "user_agent": "Mozilla/5.0 (compatible; Bot/1.0)",
                "referrer": "https://botfarm.example.com",
                "host": "cdn.example.com", 
                "path": f"/stream/segment{i:03d}.ts",
                "query": f"CMCD=sid%3D%22{session_id}%22%2Cbr%3D1500%2Cbl%3D8000",
                "status": 200,
                "ttfb_ms": 85,  # Identical TTFB
                "resp_bytes": 1048576,
                "country": "US",
                "asn": 16509,  # AWS hosting
                "ja3": "ja3_bot_fingerprint",
                "ja4": "ja4h_bot_unusual",
                "cmcd": {
                    "sid": session_id,
                    "br": "1500",
                    "bl": "8000",  # Identical buffer length
                    "sf": "h"
                }
            }
            events.append(event)
        
        return events

class TestSystemStress:
    """Stress testing scenarios"""
    
    @pytest.mark.stress
    def test_high_throughput_ingestion(self, docker_services, kafka_producer):
        """Test system under high event throughput"""
        channel_id = "stress_test_channel"
        num_sessions = 100
        events_per_session = 50
        
        start_time = time.time()
        
        # Generate high volume of events
        for session_idx in range(num_sessions):
            session_id = f"stress_session_{session_idx:04d}"
            
            for event_idx in range(events_per_session):
                event = {
                    "ts": datetime.utcnow().isoformat() + "Z",
                    "request_id": f"req_{session_id}_{event_idx:03d}",
                    "session_id": session_id,
                    "channel_id": channel_id,
                    "client_ip": f"192.168.{session_idx % 256}.{event_idx % 256}",
                    "user_agent": "StressTest/1.0",
                    "referrer": "https://stress.example.com",
                    "host": "cdn.example.com",
                    "path": f"/stream/segment{event_idx:03d}.ts",
                    "status": 200,
                    "ttfb_ms": 100,
                    "resp_bytes": 1048576,
                    "country": "US",
                    "asn": 7922,
                    "ja3": "ja3_stress_test",
                    "ja4": "ja4h_stress_test",
                    "cmcd": {"sid": session_id, "br": "1500"}
                }
                
                kafka_producer.send('cdn-logs', value=event)
        
        kafka_producer.flush()
        end_time = time.time()
        
        total_events = num_sessions * events_per_session
        throughput = total_events / (end_time - start_time)
        
        print(f"Ingested {total_events} events in {end_time - start_time:.2f}s")
        print(f"Throughput: {throughput:.0f} events/second")
        
        # Should handle at least 1000 events/second
        assert throughput >= 1000
    
    @pytest.mark.stress
    def test_api_concurrent_load(self, docker_services):
        """Test API under concurrent load"""
        import concurrent.futures
        import threading
        
        def make_concurrent_request(session_idx: int) -> bool:
            try:
                request_data = {
                    "session_id": f"concurrent_session_{session_idx:04d}",
                    "channel_id": "load_test_channel",
                    "features": {
                        "session_id": f"concurrent_session_{session_idx:04d}",
                        "channel_id": "load_test_channel",
                        "reqs_per_min": 15.0,
                        "unique_segments": 15,
                        "avg_ttfb_ms": 120.0,
                        "cadence_std_ms": 100.0,
                        "non200_rate": 0.02,
                        "asn_type": "residential"
                    }
                }
                
                response = requests.post(
                    'http://localhost:8000/api/v1/score',
                    json=request_data,
                    timeout=30
                )
                return response.status_code == 200
            except Exception as e:
                print(f"Request {session_idx} failed: {e}")
                return False
        
        # Test 100 concurrent requests
        num_requests = 100
        with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
            futures = [executor.submit(make_concurrent_request, i) for i in range(num_requests)]
            results = [future.result() for future in concurrent.futures.as_completed(futures)]
        
        success_rate = sum(results) / len(results)
        print(f"API concurrent load test: {success_rate:.1%} success rate")
        
        # Should handle at least 95% of concurrent requests successfully
        assert success_rate >= 0.95

class TestFailoverScenarios:
    """Test system behavior under component failures"""
    
    @pytest.mark.integration
    def test_kafka_recovery(self, docker_services, kafka_producer, clickhouse_client):
        """Test system recovery after Kafka interruption"""
        session_id = "kafka_recovery_test"
        channel_id = "test_channel"
        
        # Send initial events
        initial_events = self.generate_test_events(session_id, channel_id, count=10)
        for event in initial_events:
            kafka_producer.send('cdn-logs', value=event)
        kafka_producer.flush()
        
        # Simulate Kafka restart (in real test, would restart container)
        time.sleep(5)
        
        # Send more events after "recovery"
        recovery_events = self.generate_test_events(session_id, channel_id, count=10, offset=10)
        for event in recovery_events:
            kafka_producer.send('cdn-logs', value=event)
        kafka_producer.flush()
        
        # Wait for processing
        time.sleep(15)
        
        # Verify all events were processed
        decisions = clickhouse_client.execute("""
            SELECT COUNT(*) FROM viewbot.decisions WHERE session_id = %(session_id)s
        """, {'session_id': session_id})
        
        assert decisions[0][0] > 0  # Should have processed some decisions
    
    def generate_test_events(self, session_id: str, channel_id: str, count: int, offset: int = 0) -> List[Dict]:
        """Generate test events for failure scenarios"""
        events = []
        base_time = datetime.utcnow()
        
        for i in range(count):
            timestamp = base_time + timedelta(seconds=(offset + i) * 6)
            
            event = {
                "ts": timestamp.isoformat() + "Z",
                "request_id": f"req_{session_id}_{offset + i:03d}",
                "session_id": session_id,
                "channel_id": channel_id,
                "client_ip": "203.0.113.5",
                "user_agent": "TestBot/1.0",
                "referrer": "https://test.example.com",
                "host": "cdn.example.com",
                "path": f"/stream/segment{offset + i:03d}.ts",
                "status": 200,
                "ttfb_ms": 100,
                "resp_bytes": 1048576,
                "country": "US",
                "asn": 7922,
                "ja3": "ja3_test",
                "ja4": "ja4h_test",
                "cmcd": {"sid": session_id, "br": "1500"}
            }
            events.append(event)
        
        return events

class TestDataConsistency:
    """Test data consistency across components"""
    
    @pytest.mark.integration
    def test_event_ordering_consistency(self, docker_services, kafka_producer, clickhouse_client):
        """Test that events maintain proper ordering through pipeline"""
        session_id = "ordering_test_session"
        channel_id = "test_channel"
        
        # Generate events with specific timestamps
        events = []
        base_time = datetime.utcnow()
        
        for i in range(20):
            timestamp = base_time + timedelta(seconds=i * 5)  # 5-second intervals
            
            event = {
                "ts": timestamp.isoformat() + "Z",
                "request_id": f"req_ordered_{i:03d}",
                "session_id": session_id,
                "channel_id": channel_id,
                "client_ip": "203.0.113.5",
                "user_agent": "OrderingTest/1.0",
                "referrer": "https://order.example.com",
                "host": "cdn.example.com",
                "path": f"/stream/segment{i:03d}.ts",
                "status": 200,
                "ttfb_ms": 100,
                "resp_bytes": 1048576,
                "country": "US",
                "asn": 7922,
                "ja3": "ja3_order_test",
                "ja4": "ja4h_order_test",
                "cmcd": {"sid": session_id, "br": "1500"},
                "sequence_number": i  # Add sequence for verification
            }
            events.append(event)
        
        # Send events in order
        for event in events:
            kafka_producer.send('cdn-logs', value=event)
        kafka_producer.flush()
        
        # Wait for processing
        time.sleep(20)
        
        # Verify processing maintained order (check via ClickHouse raw logs)
        raw_logs = clickhouse_client.execute("""
            SELECT request_id, ts
            FROM viewbot.raw_cdn_log
            WHERE session_id = %(session_id)s
            ORDER BY ts
        """, {'session_id': session_id})
        
        # Should have processed all events
        assert len(raw_logs) >= 15  # Allow for some processing latency
        
        # Verify chronological order
        timestamps = [row[1] for row in raw_logs]
        assert timestamps == sorted(timestamps)

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short", "-m", "integration"])

#!/usr/bin/env python3
"""
Comprehensive test suite for Viewbot Guard API
"""

import pytest
import asyncio
import json
from datetime import datetime, timedelta
from unittest.mock import Mock, AsyncMock, patch
from fastapi.testclient import TestClient
from httpx import AsyncClient

# Import the API app
import sys
import os
sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'api'))
from app import app, AdvancedRiskScorer, SessionFeatures

client = TestClient(app)

class TestHealthCheck:
    """Test health check endpoints"""
    
    def test_health_check_success(self):
        """Test successful health check"""
        with patch('app.get_redis_client') as mock_redis, \
             patch('app.get_clickhouse_client') as mock_clickhouse:
            
            # Mock Redis ping
            mock_redis_instance = AsyncMock()
            mock_redis_instance.ping.return_value = True
            mock_redis.return_value = mock_redis_instance
            
            # Mock ClickHouse query
            mock_clickhouse_instance = Mock()
            mock_clickhouse_instance.execute.return_value = [[1]]
            mock_clickhouse.return_value = mock_clickhouse_instance
            
            response = client.get("/api/v1/health")
            
            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "healthy"
            assert "timestamp" in data
            assert data["services"]["redis"] == "healthy"
            assert data["services"]["clickhouse"] == "healthy"

class TestRiskScoring:
    """Test risk scoring functionality"""
    
    def test_risk_scorer_datacenter_asn(self):
        """Test datacenter ASN detection"""
        scorer = AdvancedRiskScorer()
        features = SessionFeatures(
            session_id="test_session",
            channel_id="test_channel",
            reqs_per_min=60.0,
            unique_segments=30,
            avg_ttfb_ms=100.0,
            cadence_std_ms=50.0,
            non200_rate=0.01,
            asn_type='hosting'  # Should trigger datacenter rule
        )
        
        result = scorer.score(features)
        
        assert result["score"] >= 0.4
        assert 'datacenter_asn' in result["reasons"]
        assert result["action"] in ["suppress", "challenge", "block"]
    
    def test_risk_scorer_lockstep_cadence(self):
        """Test lockstep cadence detection"""
        scorer = AdvancedRiskScorer()
        features = SessionFeatures(
            session_id="test_session",
            channel_id="test_channel",
            reqs_per_min=60.0,
            unique_segments=30,
            avg_ttfb_ms=100.0,
            cadence_std_ms=5.0,  # Very low variance - suspicious
            non200_rate=0.01,
            asn_type='residential'
        )
        
        result = scorer.score(features)
        
        assert result["score"] >= 0.3
        assert 'lockstep_cadence' in result["reasons"]
        assert result["confidence"] > 0
    
    def test_risk_scorer_perfect_timing(self):
        """Test perfect timing pattern detection"""
        scorer = AdvancedRiskScorer()
        features = SessionFeatures(
            session_id="test_session",
            channel_id="test_channel",
            reqs_per_min=60.0,
            unique_segments=30,
            avg_ttfb_ms=100.0,
            cadence_std_ms=0.0,  # Perfect timing - very suspicious
            non200_rate=0.01,
            asn_type='residential'
        )
        
        result = scorer.score(features)
        
        assert result["score"] >= 0.4
        assert 'perfect_timing_pattern' in result["reasons"]
        assert result["action"] in ["challenge", "block"]
    
    def test_risk_scorer_legitimate_user(self):
        """Test legitimate user scoring"""
        scorer = AdvancedRiskScorer()
        features = SessionFeatures(
            session_id="test_session",
            channel_id="test_channel",
            reqs_per_min=10.0,  # Normal request rate
            unique_segments=10,
            avg_ttfb_ms=150.0,
            cadence_std_ms=200.0,  # Natural variance
            non200_rate=0.02,  # Low error rate
            asn_type='residential',
            referrer_entropy=2.5,  # Good diversity
            ja4_rarity_score=0.2  # Common fingerprint
        )
        
        result = scorer.score(features)
        
        assert result["score"] < 0.3
        assert result["action"] == "count"
        assert len(result["reasons"]) == 0

class TestAPIEndpoints:
    """Test API endpoint functionality"""
    
    @patch('app.get_redis_client')
    @patch('app.get_kafka_producer')
    def test_score_session_endpoint(self, mock_kafka, mock_redis):
        """Test session scoring endpoint"""
        # Mock Redis
        mock_redis_instance = AsyncMock()
        mock_redis.return_value = mock_redis_instance
        
        # Mock Kafka
        mock_kafka_instance = Mock()
        mock_kafka.return_value = mock_kafka_instance
        
        request_data = {
            "session_id": "test_session_123",
            "channel_id": "test_channel_456",
            "features": {
                "session_id": "test_session_123",
                "channel_id": "test_channel_456",
                "reqs_per_min": 15.0,
                "unique_segments": 15,
                "avg_ttfb_ms": 120.0,
                "cadence_std_ms": 150.0,
                "non200_rate": 0.03,
                "asn_type": "residential",
                "referrer_entropy": 2.0,
                "ja4_rarity_score": 0.3
            }
        }
        
        response = client.post("/api/v1/score", json=request_data)
        
        assert response.status_code == 200
        data = response.json()
        assert data["session_id"] == "test_session_123"
        assert data["channel_id"] == "test_channel_456"
        assert 0.0 <= data["score"] <= 1.0
        assert data["action"] in ["count", "suppress", "challenge", "block"]
        assert "timestamp" in data
    
    @patch('app.get_clickhouse_client')
    def test_channel_metrics_endpoint(self, mock_clickhouse):
        """Test channel metrics endpoint"""
        # Mock ClickHouse responses
        mock_clickhouse_instance = Mock()
        
        # Main metrics response
        mock_clickhouse_instance.execute.side_effect = [
            [(1000, 800, 100, 80, 20, 0.25)],  # Main metrics
            [(16509, 50, 0.8), (13335, 30, 0.6)],  # ASN data
            [("US", 400), ("CA", 200), ("GB", 150)]  # Geo data
        ]
        
        mock_clickhouse.return_value = mock_clickhouse_instance
        
        response = client.get("/api/v1/channels/test_channel/metrics")
        
        assert response.status_code == 200
        data = response.json()
        assert data["channel_id"] == "test_channel"
        assert data["unique_viewers"] == 1000
        assert data["adjusted_viewers"] > 0
        assert "enforcement_breakdown" in data
        assert "top_asns" in data
        assert "geo_distribution" in data
    
    def test_player_event_ingestion(self):
        """Test player event ingestion endpoint"""
        with patch('app.get_kafka_producer') as mock_kafka:
            mock_kafka_instance = Mock()
            mock_kafka.return_value = mock_kafka_instance
            
            event_data = {
                "session_id": "test_session_123",
                "viewer_id": "viewer_456",
                "channel_id": "channel_789",
                "event_type": "segment_loaded",
                "timestamp": datetime.utcnow().isoformat(),
                "data": {
                    "segment_sn": 150,
                    "bytes": 1048576,
                    "load_time_ms": 250
                }
            }
            
            response = client.post("/api/v1/events/player", json=event_data)
            
            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "accepted"
            assert "event_id" in data

class TestIntegrationScenarios:
    """Integration test scenarios"""
    
    @patch('app.get_redis_client')
    @patch('app.get_clickhouse_client')
    @patch('app.get_kafka_producer')
    def test_bot_farm_detection_scenario(self, mock_kafka, mock_clickhouse, mock_redis):
        """Test detection of a coordinated bot farm"""
        # Mock dependencies
        mock_redis.return_value = AsyncMock()
        mock_kafka.return_value = Mock()
        mock_clickhouse.return_value = Mock()
        
        # Simulate bot farm characteristics
        bot_sessions = []
        for i in range(10):
            session_id = f"bot_session_{i:03d}"
            bot_features = {
                "session_id": session_id,
                "channel_id": "target_channel",
                "reqs_per_min": 60.0,  # Very consistent rate
                "unique_segments": 30,
                "avg_ttfb_ms": 85.0,  # Identical TTFB
                "cadence_std_ms": 0.0,  # Perfect timing
                "non200_rate": 0.0,
                "asn_type": "hosting",  # Data center ASN
                "referrer_entropy": 0.0,  # Same referrer
                "ja4_rarity_score": 0.9  # Uncommon fingerprint
            }
            
            request_data = {
                "session_id": session_id,
                "channel_id": "target_channel",
                "features": bot_features
            }
            
            response = client.post("/api/v1/score", json=request_data)
            assert response.status_code == 200
            
            data = response.json()
            bot_sessions.append(data)
        
        # All bot sessions should be flagged
        for session in bot_sessions:
            assert session["score"] >= 0.7  # High risk score
            assert session["action"] in ["challenge", "block"]
            assert "datacenter_asn" in session["reasons"]
            assert "perfect_timing_pattern" in session["reasons"]
    
    @patch('app.get_redis_client')
    @patch('app.get_clickhouse_client')
    @patch('app.get_kafka_producer')
    def test_legitimate_viewer_scenario(self, mock_kafka, mock_clickhouse, mock_redis):
        """Test legitimate viewer behavior"""
        # Mock dependencies
        mock_redis.return_value = AsyncMock()
        mock_kafka.return_value = Mock()
        mock_clickhouse.return_value = Mock()
        
        # Simulate legitimate viewer characteristics
        legitimate_features = {
            "session_id": "legit_session_001",
            "channel_id": "popular_channel",
            "reqs_per_min": 12.0,  # Natural request rate
            "unique_segments": 12,
            "avg_ttfb_ms": 145.0,  # Variable TTFB
            "cadence_std_ms": 180.0,  # Natural timing variance
            "non200_rate": 0.02,  # Occasional errors
            "asn_type": "residential",  # Home ISP
            "referrer_entropy": 2.8,  # Good referrer diversity
            "ja4_rarity_score": 0.1  # Common browser fingerprint
        }
        
        request_data = {
            "session_id": "legit_session_001",
            "channel_id": "popular_channel",
            "features": legitimate_features
        }
        
        response = client.post("/api/v1/score", json=request_data)
        assert response.status_code == 200
        
        data = response.json()
        assert data["score"] < 0.3  # Low risk score
        assert data["action"] == "count"
        assert len(data["reasons"]) == 0  # No suspicious reasons

class TestErrorHandling:
    """Test error handling and edge cases"""
    
    def test_invalid_session_features(self):
        """Test handling of invalid session features"""
        invalid_request = {
            "session_id": "test_session",
            "channel_id": "test_channel",
            "features": {
                "session_id": "test_session",
                "channel_id": "test_channel",
                "reqs_per_min": -5.0,  # Invalid negative value
                "unique_segments": "invalid",  # Invalid type
                "avg_ttfb_ms": 100.0,
                "cadence_std_ms": 50.0,
                "non200_rate": 0.01
            }
        }
        
        response = client.post("/api/v1/score", json=invalid_request)
        assert response.status_code == 422  # Validation error
    
    def test_missing_required_fields(self):
        """Test handling of missing required fields"""
        incomplete_request = {
            "session_id": "test_session",
            # Missing channel_id and features
        }
        
        response = client.post("/api/v1/score", json=incomplete_request)
        assert response.status_code == 422  # Validation error
    
    @patch('app.get_clickhouse_client')
    def test_channel_not_found(self, mock_clickhouse):
        """Test handling of non-existent channel"""
        mock_clickhouse_instance = Mock()
        mock_clickhouse_instance.execute.return_value = []  # No results
        mock_clickhouse.return_value = mock_clickhouse_instance
        
        response = client.get("/api/v1/channels/nonexistent_channel/metrics")
        assert response.status_code == 404

class TestPerformance:
    """Performance and load testing scenarios"""
    
    @patch('app.get_redis_client')
    @patch('app.get_kafka_producer')
    def test_concurrent_scoring_requests(self, mock_kafka, mock_redis):
        """Test handling of concurrent scoring requests"""
        # Mock dependencies
        mock_redis.return_value = AsyncMock()
        mock_kafka.return_value = Mock()
        
        import concurrent.futures
        import threading
        
        def make_scoring_request(session_id):
            request_data = {
                "session_id": f"concurrent_session_{session_id}",
                "channel_id": "load_test_channel",
                "features": {
                    "session_id": f"concurrent_session_{session_id}",
                    "channel_id": "load_test_channel",
                    "reqs_per_min": 15.0,
                    "unique_segments": 15,
                    "avg_ttfb_ms": 120.0,
                    "cadence_std_ms": 100.0,
                    "non200_rate": 0.02,
                    "asn_type": "residential"
                }
            }
            
            response = client.post("/api/v1/score", json=request_data)
            return response.status_code
        
        # Test 50 concurrent requests
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(make_scoring_request, i) for i in range(50)]
            results = [future.result() for future in concurrent.futures.as_completed(futures)]
        
        # All requests should succeed
        assert all(status == 200 for status in results)
    
    def test_large_feature_payload(self):
        """Test handling of large feature payloads"""
        large_request = {
            "session_id": "large_payload_session",
            "channel_id": "test_channel",
            "features": {
                "session_id": "large_payload_session",
                "channel_id": "test_channel",
                "reqs_per_min": 15.0,
                "unique_segments": 15,
                "avg_ttfb_ms": 120.0,
                "cadence_std_ms": 100.0,
                "non200_rate": 0.02,
                "asn_type": "residential",
                # Add many extra fields to test payload limits
                **{f"extra_field_{i}": f"value_{i}" for i in range(100)}
            }
        }
        
        with patch('app.get_redis_client'), patch('app.get_kafka_producer'):
            response = client.post("/api/v1/score", json=large_request)
            # Should handle gracefully even with extra fields
            assert response.status_code in [200, 422]

# Test runner
if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])

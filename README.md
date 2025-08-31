# Viewbot Guard - Real-time Viewbot Detection System

End-to-end, production-ready system for detecting fake viewers on livestreams. Built with modern streaming technologies and designed for scalability.

## Features

- **Real-time Detection**: Sub-10 second detection latency
- **Multi-signal Analysis**: Transport patterns, network fingerprints, behavioral analysis
- **Progressive Enforcement**: Adaptive response from counting suppression to blocking
- **Comprehensive Monitoring**: Real-time dashboards and alerting
- **Production Ready**: Complete with documentation, tests, and operational tools

## Tech Stack

- **Event Streaming**: Redpanda (Kafka API)
- **Stream Processing**: Apache Flink (PyFlink)
- **Analytics**: ClickHouse
- **State Management**: Redis
- **API**: FastAPI
- **Monitoring**: Grafana
- **Player**: hls.js with CMCD
- **Challenge System**: Cloudflare Turnstile

## Quick Start

1. Clone the repository:
```bash
git clone https://github.com/itsnothuy/Viewbot-Detector.git
cd Viewbot-Detector
```

2. Deploy services:
```bash
docker compose -f infra/docker-compose.yml up -d
```

3. Access components:
- Grafana: http://localhost:3000 (admin/admin)
- API Docs: http://localhost:8000/api/docs
- Player: http://localhost:8080
- Flink UI: http://localhost:8081

## Documentation

- [Project Charter](docs/PROJECT_CHARTER.md)
- [Requirements](docs/requirements.md)
- [System Analysis](docs/system_analysis.md)
- [Software Design](docs/software_design.md)
- [Operations Guide](docs/operations_guide.md)

## Development

### Prerequisites
- Docker 20.10+
- Docker Compose V2
- Python 3.11+
- Node.js 20+

### Local Development
```bash
# Deploy with automated testing
./scripts/deploy_and_test.sh

# Run tests
cd tests
python -m pytest test_api.py -v
python -m pytest test_integration.py -v
```

## Contributing

1. Fork the repository
2. Create your feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## Acknowledgments

- [hls.js](https://github.com/video-dev/hls.js/) for HLS playback
- [CMCD Specification](https://cdn.cta.tech/cta/media/media/resources/standards/pdfs/cta-5004-final.pdf)
- [JA3/JA4 Fingerprinting](https://github.com/FoxIO-LLC/ja4)






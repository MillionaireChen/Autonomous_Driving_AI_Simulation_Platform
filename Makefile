.PHONY: help env install-carla carla-start carla-stop carla-status smoke episode \
        episode-grpc model-dummy cut-in proto api test clean-output

# Load .env if present so CARLA_ROOT / ports / GPU come from one place.
ifneq (,$(wildcard .env))
include .env
export
endif

UV ?= uv
API_HOST ?= 127.0.0.1
API_PORT ?= 8000

help:
	@echo "Autonomous Driving AI Arena"
	@echo ""
	@echo "  make env            Create the uv virtualenv and install dependencies"
	@echo "  make install-carla  Download and unpack the CARLA server (~8 GB download)"
	@echo "  make carla-start    Start the CARLA server headless on GPU $(CARLA_GPU)"
	@echo "  make carla-status   Show whether the server is up and answering RPC"
	@echo "  make carla-stop     Stop the CARLA server"
	@echo "  make smoke          Run the Phase 1 acceptance test"
	@echo "  make episode        Run one closed-loop episode, policy in-process"
	@echo "  make model-dummy    Serve the dummy model over gRPC (foreground)"
	@echo "  make episode-grpc   Run one episode against the gRPC model service"
	@echo "  make cut-in         Run the Highway Cut-In scenario"
	@echo "  make api            Serve the REST API on :8000 (starts PostgreSQL)"
	@echo "  make proto          Regenerate the gRPC stubs from driving.proto"
	@echo "  make test           Run unit tests (no CARLA required)"
	@echo "  make clean-output   Delete generated frames and logs"

env:
	$(UV) sync
	@echo "venv ready at $(UV_PROJECT_ENVIRONMENT)"

install-carla:
	./scripts/install_carla.sh

carla-start:
	./scripts/carla_server.sh start

carla-stop:
	./scripts/carla_server.sh stop

carla-status:
	./scripts/carla_server.sh status

smoke:
	$(UV) run python scripts/carla_smoke_test.py

episode:
	$(UV) run python scripts/run_episode.py --policy dummy

model-dummy:
	$(UV) run python models/dummy/service.py --port 51001

episode-grpc:
	$(UV) run python scripts/run_episode.py --model dummy

cut-in:
	$(UV) run python scripts/run_episode.py --policy dummy \
		--scenario highway_cut_in --episode-id EP-CUTIN

api:
	$(UV) run uvicorn backend.main:app --host $(API_HOST) --port $(API_PORT)

proto:
	$(UV) run python -m grpc_tools.protoc -I. \
		--python_out=. --grpc_python_out=. --pyi_out=. \
		model_gateway/protocol/driving.proto

test:
	$(UV) run pytest -q

clean-output:
	rm -rf output logs

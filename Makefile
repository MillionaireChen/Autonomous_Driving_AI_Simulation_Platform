.PHONY: help env install-carla carla-start carla-stop carla-status smoke clean-output

# Load .env if present so CARLA_ROOT / ports / GPU come from one place.
ifneq (,$(wildcard .env))
include .env
export
endif

UV ?= uv

help:
	@echo "Autonomous Driving AI Arena - Phase 1 targets"
	@echo ""
	@echo "  make env            Create the uv virtualenv and install dependencies"
	@echo "  make install-carla  Download and unpack the CARLA server (~8 GB download)"
	@echo "  make carla-start    Start the CARLA server headless on GPU $(CARLA_GPU)"
	@echo "  make carla-status   Show whether the server is up and answering RPC"
	@echo "  make carla-stop     Stop the CARLA server"
	@echo "  make smoke          Run the Phase 1 acceptance test"
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

clean-output:
	rm -rf output logs

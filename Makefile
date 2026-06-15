# Makefile — convenience wrappers around `python3 -m fetcher`.
#
# Data always goes to the static data/ tree (data/places, data/boundaries).
#
#   make load                     # all cities x all categories (+ paris trees)
#   make load paris               # paris  x all categories (+ paris trees)
#   make load paris food          # paris  x food (no trees — category given)
#   make load all fitness         # all cities x fitness
#
# When the category is left as `all` and paris is in scope, `load` also pulls the
# Paris street-tree layer (its own separate pipeline). `make trees` runs it alone.
#
#   make boundary                 # all city boundaries
#   make boundary austin          # one city boundary
#
# `make help` lists everything.

# Prefer a local virtualenv if one exists; else fall back to system python3.
VENV_PY := $(wildcard .venv/bin/python venv/bin/python)
ifneq ($(VENV_PY),)
PYTHON ?= $(firstword $(VENV_PY))
endif
PYTHON ?= python3

FETCH := $(PYTHON) -m fetcher

CITIES   := paris nyc austin
DATASETS := food fitness

# --- Positional-arg capture for `load` ---------------------------------------
# When `load` is the goal, treat the words after it as <city> <category> rather
# than as targets. Each trailing word is turned into a no-op target so Make does
# not error with "No rule to make target". Both positions default to `all`.
ifeq (load,$(firstword $(MAKECMDGOALS)))
LOAD_ARGS := $(wordlist 2,$(words $(MAKECMDGOALS)),$(MAKECMDGOALS))
ifneq ($(LOAD_ARGS),)
$(eval $(LOAD_ARGS):;@:)
endif
endif
LOAD_CITY := $(if $(word 1,$(LOAD_ARGS)),$(word 1,$(LOAD_ARGS)),all)
LOAD_CAT  := $(if $(word 2,$(LOAD_ARGS)),$(word 2,$(LOAD_ARGS)),all)

# --- Positional-arg capture for `boundary` -----------------------------------
# `make boundary <city>` — the trailing word is the city (default all).
ifeq (boundary,$(firstword $(MAKECMDGOALS)))
BND_ARGS := $(wordlist 2,$(words $(MAKECMDGOALS)),$(MAKECMDGOALS))
ifneq ($(BND_ARGS),)
$(eval $(BND_ARGS):;@:)
endif
endif
BND_CITY := $(if $(word 1,$(BND_ARGS)),$(word 1,$(BND_ARGS)),all)

# --- Targets -----------------------------------------------------------------

.DEFAULT_GOAL := help

## load: fetch <city|all> <category|all> (both default to all)
load:
	@city='$(LOAD_CITY)'; cat='$(LOAD_CAT)'; \
	if [ "$$city" = all ] && [ "$$cat" = all ]; then \
	  $(FETCH) fetch-stores --all; \
	else \
	  [ "$$city" = all ] && sc='$(CITIES)' || sc="$$city"; \
	  [ "$$cat"  = all ] && sd='$(DATASETS)' || sd="$$cat"; \
	  first=1; \
	  for c in $$sc; do for d in $$sd; do \
	    [ $$first -eq 1 ] || { echo 'Sleeping 10s between rounds ...'; sleep 10; }; \
	    first=0; \
	    echo "--- $$c/$$d ---"; \
	    $(FETCH) fetch-stores $$c $$d || exit $$?; \
	  done; done; \
	fi; \
	if [ "$$cat" = all ] && { [ "$$city" = all ] || [ "$$city" = paris ]; }; then \
	  echo '--- paris/trees ---'; \
	  $(FETCH) fetch-trees paris || exit $$?; \
	fi

## boundary: fetch <city|all> admin boundary (default all)
boundary:
	@city='$(BND_CITY)'; \
	[ "$$city" = all ] && sc='$(CITIES)' || sc="$$city"; \
	for c in $$sc; do \
	  echo "--- boundary $$c ---"; \
	  $(FETCH) fetch-boundary $$c || exit $$?; \
	done

## trees: fetch the Paris street-tree density layer (paris-only, separate pipeline)
trees:
	$(FETCH) fetch-trees paris

## clean-bounds: drop already-committed places outside their city polygon (no network)
clean-bounds:
	$(PYTHON) bin/clean/clean-out-of-bounds.py

## help: list available targets
help:
	@echo 'city-heatmap-data — make targets'
	@echo ''
	@echo 'Load store data — make load <city> <category> (both default to all):'
	@echo '  make load                     all cities  x all categories'
	@echo '  make load <city>              one city     x all categories'
	@echo '  make load <city> <category>   one city     x one category'
	@echo '  make load all <category>      all cities   x one category'
	@echo '  (paris + all-categories also pulls the street-tree layer)'
	@echo ''
	@echo 'Boundaries — make boundary <city> (defaults to all):'
	@echo '  make boundary                 all city boundaries'
	@echo '  make boundary <city>          one city boundary'
	@echo ''
	@echo 'Trees (paris-only, separate density layer):'
	@echo '  make trees                    fetch the Paris street-tree layer'
	@echo ''
	@echo 'Maintenance:'
	@echo '  make clean-bounds             drop committed places outside city polygons (no network)'
	@echo ''
	@echo '  cities     : $(CITIES) (or all)'
	@echo '  categories : $(DATASETS) (or all)'

.PHONY: help load boundary trees clean-bounds $(CITIES) $(DATASETS) all

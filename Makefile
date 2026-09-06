# Build the site's generated files.
#
# Nothing here is needed to serve the site — public/ is already complete. This
# only regenerates what the YAML files and photos-src/ feed into it, and only
# when something they depend on has actually changed.
#
#   make            build whatever is out of date
#   make help       list the targets
#   make check      verify everything is current, without writing
#   make clean      delete the generated photos and dist/
#
# public/ is the source of truth and keeps its comments; dist/ is the copy that
# ships, with them stripped. wrangler serves dist/.
#
# Run `make help` for the full list.

SHELL := /bin/bash
.DEFAULT_GOAL := all

SRC_DIR := photos-src
OUT_DIR := public/photos
PAGE    := public/index.html
STAMP   := .make/content.stamp
DIST    := dist

YAML    := story.yaml calendar.yaml gallery.yaml
CONTENT := scripts/build-content.py
PREP    := scripts/prep-images.py
STRIP   := scripts/strip-comments.py

SOURCES := $(wildcard $(SRC_DIR)/*.jpg $(SRC_DIR)/*.jpeg $(SRC_DIR)/*.png $(SRC_DIR)/*.webp)
NAMES   := $(basename $(notdir $(SOURCES)))
FULLS   := $(addprefix $(OUT_DIR)/,$(addsuffix .jpg,$(NAMES)))
THUMBS  := $(addprefix $(OUT_DIR)/,$(addsuffix -thumb.jpg,$(NAMES)))
PHOTOS  := $(FULLS) $(THUMBS)

# One rule per source photo, each producing both derivatives in a single run.
# `&:` is a grouped target (GNU Make 4.3+): it tells make one recipe makes both
# files, so the script is not invoked twice per photo.
define photo_rule
$(OUT_DIR)/$(basename $(notdir $1)).jpg $(OUT_DIR)/$(basename $(notdir $1))-thumb.jpg &: $1 $(PREP)
	@$(PREP) $1
endef
$(foreach s,$(SOURCES),$(eval $(call photo_rule,$s)))

# The page is rendered from the YAML, and the gallery tiles point at the
# thumbnails, so the photos have to exist first. Tracked with a stamp rather
# than by making $(PAGE) a target: the page is partly hand-written, and
# build-content.py leaves it untouched when nothing changed, which would keep
# make thinking it was still out of date.
$(STAMP): $(YAML) $(CONTENT) $(PHOTOS)
	@mkdir -p $(dir $@)
	@$(CONTENT)
	@touch $@

.PHONY: all photos content dist check test test-dist clean help

all: dist ## Build everything that is out of date (default)

photos: $(PHOTOS) ## Only the web-sized photos and their thumbnails

content: $(STAMP) ## Only render the YAML files into the page

# Phony on purpose: the target shares its name with the directory, and make
# would otherwise call it up to date the moment dist/ exists.
dist: content ## Mirror public/ into dist/ with the HTML comments stripped
	@$(STRIP)

check: ## Verify photos and page are current; exits non-zero if not
	@$(PREP) --check
	@$(CONTENT) --check
	@$(STRIP) --check

test: content ## Run the smoke tests against a headless browser
	@./tests/smoke.py

test-dist: dist ## Run the same checks against the stripped copy that ships
	@./tests/smoke.py --dir $(DIST)

clean: ## Delete the generated photos, dist/ and the build stamp
	@rm -rf $(OUT_DIR) .make $(DIST)
	@echo "clean: removed $(OUT_DIR)/, $(DIST)/ and .make/"
	@echo "       photos-src/ and the YAML files are untouched; run make to rebuild."
	@echo "       note: the generated blocks inside $(PAGE) stay as they are —"
	@echo "       make rewrites them, and 'make clean' will not blank the page."

help: ## Show this help
	@echo "Spellbinding site build"
	@echo
	@grep -hE '^[a-z][a-zA-Z_-]*:.*## ' $(MAKEFILE_LIST) \
	  | sed 's/:[^#]*## /|/' \
	  | awk -F'|' '{ printf "  make %-9s %s\n", $$1, $$2 }'
	@echo
	@echo "  Sources:  $(SRC_DIR)/  $(YAML)"
	@echo "  Outputs:  $(OUT_DIR)/  and the marked blocks in $(PAGE)"
	@printf "  Photos:   %s source(s) -> %s generated file(s)\n" \
	  "$(words $(SOURCES))" "$(words $(PHOTOS))"

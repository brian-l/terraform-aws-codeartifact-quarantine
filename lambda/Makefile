# Lambda build step.
#
# Each handler's source dir gets a copy of common/ before archive_file zips it.
# Run `make` before `terraform apply` if you've changed any Lambda source.
# CI workflows should run this as part of the plan step.

HANDLERS := ingestion scan promote audit expedite approve

.PHONY: all clean $(HANDLERS)

all: $(HANDLERS)

$(HANDLERS):
	@echo "Bundling common/ into $@/"
	@rm -rf $@/common
	@cp -r common $@/common
	@find $@ -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true

clean:
	@for h in $(HANDLERS); do rm -rf $$h/common $$h/__pycache__; done

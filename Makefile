.PHONY: firmware left right reset clean

firmware:
	@./scripts/zmk-build all

left:
	@./scripts/zmk-build left

right:
	@./scripts/zmk-build right

reset:
	@./scripts/zmk-build reset

clean:
	@./scripts/zmk-build clean

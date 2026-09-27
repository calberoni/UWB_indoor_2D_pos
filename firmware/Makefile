# Firmware de los tres nodos.
#
#   make all                      compila los tres perfiles
#   make tag | anchor_a | anchor_b
#   make flash ROLE=tag [PORT=/dev/cu.usbmodemXXXX]
#   make test                     pruebas de la aritmética en el Mac
#
# Si no se indica PORT, el puerto se busca en nodes.yaml por el número de serie
# de la placa (ver tools/nodes.py).

FQBN    := arduino:mbed_nano:nano33ble
SKETCH  := uwb_node
DRIVER  := lib/decadriver
BUILD   := build
PYTHON  := ../.venv/bin/python

ROLES   := anchor_a anchor_b tag
DEFINE_anchor_a := ROLE_ANCHOR_A
DEFINE_anchor_b := ROLE_ANCHOR_B
DEFINE_tag      := ROLE_TAG

SOURCES := $(wildcard $(SKETCH)/*)

.PHONY: all $(ROLES) flash test clean

all: $(ROLES)

$(ROLES): %: $(BUILD)/%/$(SKETCH).ino.bin

$(DRIVER)/library.properties:
	../tools/fetch_driver.sh

# Se compila con todos los avisos activados y se exige que el código propio no
# produzca ninguno; los del driver y del core no se pueden corregir aquí.
$(BUILD)/%/$(SKETCH).ino.bin: $(SOURCES) $(DRIVER)/library.properties
	@mkdir -p $(BUILD)/$*
	arduino-cli compile --fqbn $(FQBN) --library $(DRIVER) \
		--build-path $(BUILD)/$* --warnings all \
		--build-property "build.extra_flags=-DROLE=$(DEFINE_$*) -DCONFIG_DW3000_CHIP_DW3000=1" \
		$(SKETCH) 2> $(BUILD)/$*/compile.log || (cat $(BUILD)/$*/compile.log; exit 1)
	@if grep -E "$(SKETCH)/[^ ]*: *warning:|sketch/[^ ]*: *warning:" $(BUILD)/$*/compile.log; then \
		echo "error: el código propio produce avisos (perfil $*)"; \
		rm -f $@; exit 1; \
	fi

flash:
ifndef ROLE
	$(error Indica el rol: make flash ROLE=tag)
endif
	$(MAKE) $(ROLE)
	@port="$(PORT)"; \
	if [ -z "$$port" ]; then port=$$($(PYTHON) ../tools/nodes.py port $(ROLE)) || exit 1; fi; \
	echo "grabando $(ROLE) en $$port"; \
	arduino-cli upload --fqbn $(FQBN) -p $$port --input-dir $(BUILD)/$(ROLE)

test:
	@mkdir -p $(BUILD)/tests
	cc -std=c11 -Wall -Wextra -Werror -I$(SKETCH) -o $(BUILD)/tests/test_math \
		tests/test_math.c $(SKETCH)/twr_math.c $(SKETCH)/record.c -lm
	$(BUILD)/tests/test_math

clean:
	rm -rf $(BUILD)

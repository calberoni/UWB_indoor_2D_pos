# UWB Indoor Positioning
#
#   make setup                     instala herramientas y dependencias
#   make firmware                  compila los cuatro nodos
#   make flash ROLE=tag            graba un nodo (anchor_a, anchor_b, anchor_c o tag)
#   make nodes                     placas conectadas y su rol
#   make demo                      bridge por BLE y dashboard en el navegador
#   make demo SERIAL=1             lo mismo por USB serie
#   make replay LOG=logs/x.csv     reproduce una sesión grabada, sin hardware
#   make sim                       regenera los logs de ejemplo
#   make test                      pruebas del firmware y del bridge
#   make gif VIDEO=captura.webm    convierte una grabación del dashboard a GIF

PYTHON := .venv/bin/python
BRIDGE := PYTHONPATH=bridge $(PYTHON) -m uwb_bridge
LOG    ?= logs/ejemplo-paseo.csv

.PHONY: setup firmware anchor_a anchor_b anchor_c tag flash nodes demo replay sim test gif clean

setup:
	tools/setup.sh

firmware:
	$(MAKE) -C firmware all

anchor_a anchor_b anchor_c tag:
	$(MAKE) -C firmware $@

flash:
	$(MAKE) -C firmware flash ROLE=$(ROLE) PORT=$(PORT)

nodes:
	$(PYTHON) tools/nodes.py list

demo:
ifdef SERIAL
	$(BRIDGE) --serial $(or $(PORT),$(shell $(PYTHON) tools/nodes.py port tag)) --open
else
	$(BRIDGE) --ble --open
endif

replay:
	$(BRIDGE) --replay $(LOG) --open

sim:
	$(PYTHON) tools/sim.py rect --out logs/ejemplo-rectangulo.csv
	$(PYTHON) tools/sim.py walk --out logs/ejemplo-paseo.csv

test:
	$(MAKE) -C firmware test
	$(PYTHON) -m pytest bridge/tests -q

gif:
ifndef VIDEO
	$(error Indica el vídeo: make gif VIDEO=captura.webm)
endif
	ffmpeg -y -i $(VIDEO) \
		-vf "fps=15,scale=960:-1:flags=lanczos,split[a][b];[a]palettegen[p];[b][p]paletteuse" \
		$(basename $(VIDEO)).gif

clean:
	$(MAKE) -C firmware clean

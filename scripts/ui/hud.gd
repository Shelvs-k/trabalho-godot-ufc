extends CanvasLayer

@onready var barra_vida: TextureProgressBar = $Interface/BarraVida

var jogador: Node


func _ready() -> void:
	jogador = get_tree().get_first_node_in_group("player")
	if jogador == null:
		push_warning("HUD: nenhum jogador foi encontrado no grupo 'player'.")
		return

	if jogador.has_signal("vida_atualizada"):
		jogador.connect("vida_atualizada", _atualizar_vida)

	_atualizar_vida(int(jogador.get("vida")), int(jogador.get("vida_maxima")))


func _atualizar_vida(vida_atual: int, vida_maxima: int) -> void:
	barra_vida.max_value = vida_maxima
	barra_vida.value = vida_atual

extends CanvasLayer

@onready var barra_vida: TextureProgressBar = $Interface/BarraVida
@onready var aviso_vida_baixa: Label = $AvisoVidaBaixa

var jogador: Node
var animacao_aviso: Tween


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
	var vida_baixa = vida_maxima > 0 and vida_atual * 100 <= vida_maxima * 20
	var cor = aviso_vida_baixa.modulate

	if vida_baixa:
		if aviso_vida_baixa.visible:
			return
		aviso_vida_baixa.visible = true
		cor.a = 0.15
		aviso_vida_baixa.modulate = cor
		animacao_aviso = create_tween().set_loops()
		animacao_aviso.tween_property(aviso_vida_baixa, "modulate:a", 1.0, 0.5).set_trans(Tween.TRANS_SINE).set_ease(Tween.EASE_IN_OUT)
		animacao_aviso.tween_property(aviso_vida_baixa, "modulate:a", 0.15, 0.5).set_trans(Tween.TRANS_SINE).set_ease(Tween.EASE_IN_OUT)
	else:
		if animacao_aviso:
			animacao_aviso.kill()
			animacao_aviso = null
		aviso_vida_baixa.visible = false
		cor.a = 1.0
		aviso_vida_baixa.modulate = cor

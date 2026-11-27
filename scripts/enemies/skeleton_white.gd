extends Inimigo

@export_group("Movimento")
@export var velocidade: float = 100.0
@export var distancia_ataque: float = 55.0
@export var distancia_vertical_ataque: float = 100.0

@export_group("Combate")
@export var intervalo_ataque: float = 0.8

@export_group("Frames de ataque")
@export var frames_attack_01: Array[int] = []
@export var frames_attack_02: Array[int] = []

enum Estado { IDLE, WALK, ATTACK, HURT }

var estado: Estado = Estado.IDLE
var hitbox_offset_x: float
var ataque_atual: String = "attack_01"
var proximo_ataque: String = "attack_02"
var tempo_ataque: float = 0.0
var hitboxes_atingidas: Array[Node] = []


func _ready() -> void:
	super._ready()
	hitbox_offset_x = absf(hitbox_shape.position.x)
	sprite.frame_changed.connect(_atualizar_hitbox)
	sprite.animation_finished.connect(_on_animation_finished)
	sprite.play("idle")


func _atualizar_orientacao_hitbox() -> void:
	hitbox_shape.position.x = -hitbox_offset_x if sprite.flip_h else hitbox_offset_x


func _physics_process(delta: float) -> void:
	if inimigo_morto:
		return

	aplicar_gravidade(delta)
	tempo_ataque = maxf(tempo_ataque - delta, 0.0)

	match estado:
		Estado.IDLE, Estado.WALK:
			_atualizar_movimento()
		Estado.ATTACK:
			velocity.x = 0.0
		Estado.HURT:
			velocity.x = move_toward(velocity.x, 0.0, 1000.0 * delta)

	move_and_slide()
	_verificar_jogador_na_hitbox()


func _atualizar_movimento() -> void:
	var deteccao := detectar_reimu()
	if not deteccao.detectada:
		estado = Estado.IDLE
		velocity.x = 0.0
		_tocar_animacao("idle")
		return

	var distancia_horizontal := absf(reimu.global_position.x - global_position.x)
	var distancia_vertical := absf(reimu.global_position.y - global_position.y)
	if distancia_horizontal <= distancia_ataque \
	and distancia_vertical <= distancia_vertical_ataque \
	and tempo_ataque <= 0.0:
		_iniciar_ataque()
		return

	if distancia_horizontal > distancia_ataque:
		estado = Estado.WALK
		velocity.x = deteccao.direcao * velocidade
		sprite.flip_h = deteccao.direcao < 0.0
		_atualizar_orientacao_hitbox()
		_tocar_animacao("walk")
	else:
		estado = Estado.IDLE
		velocity.x = move_toward(velocity.x, 0.0, 500.0)
		_tocar_animacao("idle")


func _iniciar_ataque() -> void:
	estado = Estado.ATTACK
	velocity.x = 0.0
	tempo_ataque = intervalo_ataque
	ataque_atual = proximo_ataque
	proximo_ataque = "attack_02" if proximo_ataque == "attack_01" else "attack_01"

	sprite.flip_h = reimu.global_position.x < global_position.x
	_atualizar_orientacao_hitbox()
	hitboxes_atingidas.clear()
	desativar_hitbox()
	_tocar_animacao(ataque_atual)


func _atualizar_hitbox() -> void:
	if estado != Estado.ATTACK:
		desativar_hitbox()
		return

	var frames_ativos: Array[int]
	if sprite.animation == "attack_01":
		frames_ativos = frames_attack_01
	elif sprite.animation == "attack_02":
		frames_ativos = frames_attack_02
	else:
		desativar_hitbox()
		return

	definir_hitbox_ativa(sprite.frame in frames_ativos)


func _verificar_jogador_na_hitbox() -> void:
	if not hitbox.monitoring or hitbox_shape.disabled:
		return
	for area in hitbox.get_overlapping_areas():
		acertar_reimu(area, hitboxes_atingidas)


func receber_dano(valor: int, origem: Vector2) -> void:
	if inimigo_morto:
		return
	super.receber_dano(valor, origem)
	if not inimigo_morto:
		estado = Estado.HURT


func _on_animation_finished() -> void:
	if inimigo_morto:
		finalizar_animacao_morte()
		return

	match sprite.animation:
		"attack_01", "attack_02":
			desativar_hitbox()
			estado = Estado.IDLE
		"hurt":
			if estado == Estado.HURT:
				estado = Estado.IDLE


func _tocar_animacao(nome: String) -> void:
	if sprite.animation != nome:
		sprite.play(nome)

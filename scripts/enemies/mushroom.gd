extends Inimigo

@export_group("Movimento")
@export var velocidade: float = 100.0
@export var distancia_ataque: float = 55.0
@export var distancia_vertical_ataque: float = 100.0

@export_group("Investida do ataque")
@export var velocidade_investida: float = 180.0

@export_group("Combate")
@export var intervalo_ataque: float = 0.8

@export_group("Frames de ataque")
@export var frames_attack: Array[int] = [5, 6, 7]

enum Estado { IDLE, WALK, ATTACK, HURT }

var estado: Estado = Estado.IDLE
var posicao_base_hitbox: Vector2
var direcao_investida: float = 1.0
var tempo_ataque: float = 0.0
var hitboxes_atingidas: Array[Node] = []


func _ready() -> void:
	super._ready()
	posicao_base_hitbox = hitbox_shape.position
	hitbox.area_entered.connect(_on_hitbox_area_entered)
	sprite.frame_changed.connect(_atualizar_hitbox)
	sprite.animation_finished.connect(_on_animation_finished)
	sprite.play("idle")


func _atualizar_orientacao_hitbox() -> void:
	hitbox_shape.position = posicao_base_hitbox
	if sprite.flip_h:
		hitbox_shape.position.x = -posicao_base_hitbox.x


func _physics_process(delta: float) -> void:
	if inimigo_morto:
		return
	aplicar_gravidade(delta)
	tempo_ataque = maxf(tempo_ataque - delta, 0.0)

	match estado:
		Estado.IDLE, Estado.WALK:
			_atualizar_movimento()
		Estado.ATTACK:
			if sprite.animation == "attack" and sprite.frame in frames_attack:
				velocity.x = direcao_investida * velocidade_investida
			else:
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
		sprite.flip_h = deteccao.direcao > 0.0
		_atualizar_orientacao_hitbox()
		_tocar_animacao("walk")
	else:
		estado = Estado.IDLE
		velocity.x = move_toward(velocity.x, 0.0, 500.0)
		_tocar_animacao("idle")


func _iniciar_ataque() -> void:
	estado = Estado.ATTACK
	tempo_ataque = intervalo_ataque
	velocity.x = 0.0
	if is_instance_valid(reimu):
		sprite.flip_h = reimu.global_position.x > global_position.x
		_atualizar_orientacao_hitbox()
	direcao_investida = 1.0 if sprite.flip_h else -1.0
	hitboxes_atingidas.clear()
	desativar_hitbox()
	_tocar_animacao("attack")


func _atualizar_hitbox() -> void:
	if estado != Estado.ATTACK or sprite.animation != "attack":
		desativar_hitbox()
		return
	definir_hitbox_ativa(sprite.frame in frames_attack)


func _on_hitbox_area_entered(area: Area2D) -> void:
	acertar_reimu(area, hitboxes_atingidas)


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
		"attack":
			desativar_hitbox()
			velocity.x = 0.0
			estado = Estado.IDLE
		"hurt":
			if estado == Estado.HURT:
				estado = Estado.IDLE


func _tocar_animacao(nome: String) -> void:
	if sprite.animation != nome:
		sprite.play(nome)

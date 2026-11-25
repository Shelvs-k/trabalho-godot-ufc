extends CharacterBody2D

@onready var sprite: AnimatedSprite2D = $Sprite
@onready var hurtbox: Area2D = $Hurtbox
@onready var hitbox: Area2D = $Hitbox
@onready var hitbox_shape: CollisionShape2D = $"Hitbox/Colisão"
@onready var physics_shape: CollisionShape2D = $"Colisão"

@export_group("Movimento")
@export var velocidade: float = 100.0
@export var distancia_ataque: float = 55.0
@export var distancia_vertical_ataque: float = 100.0

@export_group("Investida do ataque")
@export var velocidade_investida: float = 180.0

@export_group("Combate")
@export var vida_maxima: int = 3
@export var dano: int = 1
@export var intervalo_ataque: float = 0.8
@export var knockback_horizontal: float = 250.0
@export var knockback_vertical: float = 150.0
@export var gravidade: float = 980.0

@export_group("Frames de ataque")
@export var frames_attack: Array[int] = [5, 6, 7]

enum Estado {
	IDLE,
	WALK,
	ATTACK,
	HURT,
	DIE
}

var estado: Estado = Estado.IDLE
var vida: int
var jogador: CharacterBody2D
var posicao_base_hitbox: Vector2
var direcao_investida: float = 1.0
var tempo_ataque: float = 0.0
var hitboxes_atingidas: Array[Node] = []


func _ready() -> void:
	vida = vida_maxima

	add_to_group("enemies")

	hitbox_shape.disabled = true
	hitbox.monitoring = false
	posicao_base_hitbox = hitbox_shape.position

	hurtbox.area_entered.connect(_on_hurtbox_area_entered)
	hitbox.area_entered.connect(_on_hitbox_area_entered)

	sprite.frame_changed.connect(_atualizar_hitbox)
	sprite.animation_finished.connect(_on_animation_finished)

	jogador = get_tree().get_first_node_in_group("player")
	sprite.play("idle")


func _atualizar_orientacao_hitbox() -> void:
	# O sprite original aponta para a esquerda; flip_h o faz olhar para a direita.
	hitbox_shape.position = posicao_base_hitbox
	if sprite.flip_h:
		hitbox_shape.position.x = -posicao_base_hitbox.x


func _physics_process(delta: float) -> void:
	if estado == Estado.DIE:
		return

	if not is_on_floor():
		velocity.y += gravidade * delta

	if tempo_ataque > 0.0:
		tempo_ataque -= delta

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
	if not is_instance_valid(jogador):
		jogador = get_tree().get_first_node_in_group("player")

	if not is_instance_valid(jogador):
		estado = Estado.IDLE
		velocity.x = move_toward(velocity.x, 0.0, 500.0)
		_tocar_animacao("idle")
		return

	var direcao := signf(jogador.global_position.x - global_position.x)
	var distancia_horizontal := absf(jogador.global_position.x - global_position.x)
	var distancia_vertical := absf(jogador.global_position.y - global_position.y)

	if distancia_horizontal <= distancia_ataque \
	and distancia_vertical <= distancia_vertical_ataque \
	and tempo_ataque <= 0.0:
		_iniciar_ataque()
		return

	if distancia_horizontal > distancia_ataque:
		estado = Estado.WALK
		velocity.x = direcao * velocidade
		sprite.flip_h = direcao > 0.0
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

	if is_instance_valid(jogador):
		sprite.flip_h = jogador.global_position.x > global_position.x
		_atualizar_orientacao_hitbox()

	direcao_investida = 1.0 if sprite.flip_h else -1.0
	hitboxes_atingidas.clear()
	_desativar_hitbox()
	_tocar_animacao("attack")


func _atualizar_hitbox() -> void:
	if estado != Estado.ATTACK or sprite.animation != "attack":
		_desativar_hitbox()
		return

	var frame_ativo := sprite.frame in frames_attack
	hitbox_shape.set_deferred("disabled", not frame_ativo)
	hitbox.set_deferred("monitoring", frame_ativo)


func _desativar_hitbox() -> void:
	hitbox_shape.set_deferred("disabled", true)
	hitbox.set_deferred("monitoring", false)


func _on_hitbox_area_entered(area: Area2D) -> void:
	var alvo := area.get_parent()
	if not is_instance_valid(alvo) or not alvo.is_in_group("player"):
		return
	if alvo in hitboxes_atingidas:
		return

	hitboxes_atingidas.append(alvo)
	if alvo.has_method("receber_dano"):
		alvo.receber_dano(dano, global_position)


func _verificar_jogador_na_hitbox() -> void:
	if not hitbox.monitoring or hitbox_shape.disabled:
		return

	for area in hitbox.get_overlapping_areas():
		_on_hitbox_area_entered(area)


func _on_hurtbox_area_entered(area: Area2D) -> void:
	if not area.is_in_group("player_hitbox"):
		return

	var atacante := area.get_parent()
	if not is_instance_valid(atacante):
		return

	# Deixa o jogador controlar a deduplicação por golpe. Isso mantém a
	# detecção por sinal como fallback e evita dano duplicado na checagem contínua.
	if atacante.has_method("registrar_acerto"):
		atacante.registrar_acerto(self)
		return

	var dano_recebido := 1
	if atacante.has_method("get"):
		var valor = atacante.get("dano_ataque")
		if valor != null:
			dano_recebido = valor

	receber_dano(dano_recebido, atacante.global_position)


func receber_dano(valor: int, origem: Vector2) -> void:
	if estado == Estado.DIE:
		return

	vida = maxi(0, vida - valor)
	if vida <= 0:
		morrer()
		return

	estado = Estado.HURT
	_desativar_hitbox()

	var direcao := signf(global_position.x - origem.x)
	if is_zero_approx(direcao):
		direcao = 1.0 if sprite.flip_h else -1.0

	velocity.x = direcao * knockback_horizontal
	velocity.y = -knockback_vertical
	sprite.play("hurt")


func morrer() -> void:
	estado = Estado.DIE
	_desativar_hitbox()
	hurtbox.set_deferred("monitoring", false)
	hurtbox.set_deferred("monitorable", false)
	$"Hurtbox/Colisão".set_deferred("disabled", true)
	physics_shape.set_deferred("disabled", true)
	velocity = Vector2.ZERO
	sprite.play("die")


func _on_animation_finished() -> void:
	match sprite.animation:
		"attack":
			_desativar_hitbox()
			velocity.x = 0.0
			estado = Estado.IDLE
		"hurt":
			if estado == Estado.HURT:
				estado = Estado.IDLE
		"die":
			queue_free()


func _tocar_animacao(nome: String) -> void:
	if sprite.animation != nome:
		sprite.play(nome)

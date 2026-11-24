extends CharacterBody2D

@onready var animReimu = $Sprite

@export var speed = 250.0

enum EstadoAnim {
	IDLE,
	WALK_S,
	WALK_L,
	WALK_E,
	JUMP_S,
	JUMP_L,
	JUMP_E,
	DASH_S,
	DASH_L,
	DASH_E,
	ATTACK_01,
	ATTACK_02,
	ATTACK_AIR
}
var estadoAnimacao = EstadoAnim.IDLE

func _physics_process(delta: float) -> void:
	var currentAnim: String
	var direction = Input.get_axis("left", "right")
	# Movimento horizontal e orientação
	velocity.x = direction * speed
	if direction != 0:
		animReimu.flip_h = direction > 0
	# Controle dos estados de animação
	if direction != 0:
		if estadoAnimacao == EstadoAnim.IDLE or estadoAnimacao == EstadoAnim.WALK_E:
			estadoAnimacao = EstadoAnim.WALK_S
	else:
		if estadoAnimacao == EstadoAnim.WALK_S or estadoAnimacao == EstadoAnim.WALK_L:
			estadoAnimacao = EstadoAnim.WALK_E
	# Seleção da animação
	match estadoAnimacao:
		EstadoAnim.IDLE:
			currentAnim = "idle"
		EstadoAnim.WALK_S:
			currentAnim = "walk_s"
		EstadoAnim.WALK_L:
			currentAnim = "walk_l"
		EstadoAnim.WALK_E:
			currentAnim = "walk_e"
	# Evita reiniciar a animação desnecessariamente
	if animReimu.animation != currentAnim:
		animReimu.play(currentAnim)
	move_and_slide()

func _on_sprite_animation_finished() -> void:
	match animReimu.animation:
		"walk_s":
			if Input.get_axis("left", "right") != 0:
				estadoAnimacao = EstadoAnim.WALK_L
			else:
				estadoAnimacao = EstadoAnim.WALK_E
		"walk_e":
			estadoAnimacao = EstadoAnim.IDLE

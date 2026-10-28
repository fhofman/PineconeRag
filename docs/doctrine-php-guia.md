# Doctrine para PHP — Guía completa

> Referencia práctica de lo más importante de Doctrine (ORM, DBAL y Migrations), con ejemplos y un resumen de cambios por versión.
> Versiones estables a octubre de 2026: **ORM 3.6.x**, **ORM 2.20.x** (rama de mantenimiento), **DBAL 4.4.x**, **Migrations 3.x**.

---

## Índice

1. [Qué es Doctrine](#1-qué-es-doctrine)
2. [Instalación y configuración](#2-instalación-y-configuración)
3. [Arquitectura: EntityManager y UnitOfWork](#3-arquitectura-entitymanager-y-unitofwork)
4. [Mapeo de entidades](#4-mapeo-de-entidades)
5. [Relaciones (asociaciones)](#5-relaciones-asociaciones)
6. [Herencia](#6-herencia)
7. [Repositorios](#7-repositorios)
8. [Consultas: DQL, QueryBuilder y SQL nativo](#8-consultas-dql-querybuilder-y-sql-nativo)
9. [Hidratación y DTOs](#9-hidratación-y-dtos)
10. [Performance](#10-performance)
11. [Eventos y listeners](#11-eventos-y-listeners)
12. [Transacciones y concurrencia](#12-transacciones-y-concurrencia)
13. [Doctrine DBAL](#13-doctrine-dbal)
14. [Doctrine Migrations](#14-doctrine-migrations)
15. [Otros paquetes del ecosistema](#15-otros-paquetes-del-ecosistema)
16. [Buenas prácticas y errores comunes](#16-buenas-prácticas-y-errores-comunes)
17. [Cambios por versión](#17-cambios-por-versión)
18. [Checklist de migración ORM 2 → 3 y DBAL 3 → 4](#18-checklist-de-migración-orm-2--3-y-dbal-3--4)

---

## 1. Qué es Doctrine

Doctrine es un conjunto de librerías PHP para trabajar con bases de datos. Las piezas principales:

| Paquete | Para qué sirve |
|---|---|
| `doctrine/orm` | Object-Relational Mapper: mapea objetos PHP (entidades) a tablas, con patrón **Data Mapper**. |
| `doctrine/dbal` | Database Abstraction Layer: capa sobre PDO/mysqli/etc. con QueryBuilder SQL, tipos y gestión de esquema. El ORM está construido encima. |
| `doctrine/migrations` | Versionado del esquema de base de datos. |
| `doctrine/collections` | `Collection`, `ArrayCollection`, `Criteria` (usadas en relaciones). |
| `doctrine/persistence` | Interfaces comunes (`ObjectManager`, `ObjectRepository`, `ManagerRegistry`). |
| `doctrine/data-fixtures` | Carga de datos de prueba. |
| `doctrine/mongodb-odm` | Equivalente al ORM para MongoDB. |

**Data Mapper vs Active Record:** a diferencia de Eloquent (Laravel), las entidades de Doctrine son objetos PHP "planos" que no saben guardarse a sí mismos. Toda la persistencia la gestiona el `EntityManager`. Esto mantiene el dominio desacoplado de la base de datos.

---

## 2. Instalación y configuración

```bash
composer require doctrine/orm doctrine/dbal symfony/cache
composer require --dev doctrine/migrations
```

### Configuración standalone (ORM 3)

```php
<?php
use Doctrine\DBAL\DriverManager;
use Doctrine\ORM\EntityManager;
use Doctrine\ORM\ORMSetup;

require 'vendor/autoload.php';

$config = ORMSetup::createAttributeMetadataConfiguration(
    paths: [__DIR__ . '/src/Entity'],
    isDevMode: true,
);

// En PHP 8.4+ se recomienda usar lazy objects nativos
// $config->enableNativeLazyObjects(true);

$connection = DriverManager::getConnection([
    'driver'   => 'pdo_mysql',
    'host'     => '127.0.0.1',
    'dbname'   => 'app',
    'user'     => 'root',
    'password' => 'secret',
    'serverVersion' => '8.0',
], $config);

$entityManager = new EntityManager($connection, $config);
```

> `EntityManager::create()` ya no existe en ORM 3: se usa `new EntityManager(...)`.
> En DBAL 4 el parámetro `url` se eliminó; para DSN usar `Doctrine\DBAL\Tools\DsnParser`.

```php
use Doctrine\DBAL\Tools\DsnParser;

$params = (new DsnParser(['mysql' => 'pdo_mysql']))
    ->parse('mysql://root:secret@127.0.0.1/app');
```

### Con Symfony

```bash
composer require symfony/orm-pack
composer require --dev symfony/maker-bundle
```

```yaml
# config/packages/doctrine.yaml
doctrine:
  dbal:
    url: '%env(resolve:DATABASE_URL)%'
  orm:
    mappings:
      App:
        type: attribute
        dir: '%kernel.project_dir%/src/Entity'
        prefix: 'App\Entity'
```

### Consola

```bash
vendor/bin/doctrine orm:schema-tool:create      # crea el esquema
vendor/bin/doctrine orm:schema-tool:update --dump-sql
vendor/bin/doctrine orm:validate-schema         # valida mapeo y sincronía con la DB
vendor/bin/doctrine orm:mapping:describe User   # (3.6: --format=json)
```

---

## 3. Arquitectura: EntityManager y UnitOfWork

### EntityManager
Es la puerta de entrada. Métodos clave:

| Método | Qué hace |
|---|---|
| `persist($entity)` | Marca una entidad **nueva** para ser insertada. No ejecuta SQL. |
| `remove($entity)` | Marca para borrar. |
| `flush()` | Calcula los cambios y ejecuta todo el SQL en una transacción. |
| `find(Class, $id)` | Busca por clave primaria (usa primero el Identity Map). |
| `getReference(Class, $id)` | Devuelve un proxy sin consultar la DB. Útil para asignar relaciones. |
| `refresh($entity)` | Recarga el estado desde la DB. |
| `detach($entity)` | Deja de gestionar la entidad. |
| `clear()` | Desconecta **todas** las entidades (libera memoria). |
| `wrapInTransaction(callable)` | Ejecuta dentro de una transacción con flush y rollback automático. |
| `getRepository(Class)` | Obtiene el repositorio. |

### UnitOfWork
Lleva el registro de qué cambió. En el `flush()`:
1. Calcula el *changeset* de cada entidad gestionada (por defecto comparando contra una copia del estado original: política **DEFERRED_IMPLICIT**).
2. Ordena las operaciones (inserts respetando dependencias, luego updates, luego deletes).
3. Ejecuta todo en una transacción.

### Identity Map
Dentro de un mismo EntityManager, **una fila = una única instancia**. Si hacés `find(User::class, 1)` dos veces, obtenés el mismo objeto y la segunda vez no hay consulta.

### Estados de una entidad

```
NEW ──persist()──▶ MANAGED ──remove()──▶ REMOVED ──flush()──▶ (borrada)
                     │  ▲
           detach()/ │  │ (find/consulta)
            clear()  ▼  │
                   DETACHED
```

- **NEW:** objeto creado con `new`, Doctrine no lo conoce.
- **MANAGED:** gestionado; los cambios se detectan en el `flush()`.
- **DETACHED:** existe en la DB pero el EM no lo sigue.
- **REMOVED:** se borrará en el próximo `flush()`.

> `merge()` se eliminó en ORM 3. Para reasociar datos, volvé a cargar la entidad y aplicale los cambios.

---

## 4. Mapeo de entidades

Desde ORM 3 el mapeo se hace con **atributos PHP 8** o **XML**. Las anotaciones (docblocks) y YAML ya no existen.

```php
<?php
namespace App\Entity;

use App\Repository\ProductRepository;
use Doctrine\DBAL\Types\Types;
use Doctrine\ORM\Mapping as ORM;

#[ORM\Entity(repositoryClass: ProductRepository::class)]
#[ORM\Table(name: 'products')]
#[ORM\Index(name: 'idx_product_name', columns: ['name'])]
#[ORM\UniqueConstraint(name: 'uniq_product_sku', columns: ['sku'])]
#[ORM\HasLifecycleCallbacks]
class Product
{
    #[ORM\Id]
    #[ORM\GeneratedValue]
    #[ORM\Column]
    private ?int $id = null;

    #[ORM\Column(length: 255)]
    private string $name;

    #[ORM\Column(length: 50)]
    private string $sku;

    #[ORM\Column(type: Types::DECIMAL, precision: 10, scale: 2)]
    private string $price; // DECIMAL se hidrata como string para no perder precisión

    #[ORM\Column(type: Types::TEXT, nullable: true)]
    private ?string $description = null;

    #[ORM\Column(enumType: ProductStatus::class)]
    private ProductStatus $status = ProductStatus::Draft;

    #[ORM\Column(type: Types::JSON)]
    private array $attributes = [];

    #[ORM\Column(type: Types::DATETIME_IMMUTABLE)]
    private \DateTimeImmutable $createdAt;

    #[ORM\Version]
    #[ORM\Column]
    private int $version = 1; // para locking optimista

    public function __construct(string $name, string $sku, string $price)
    {
        $this->name = $name;
        $this->sku = $sku;
        $this->price = $price;
        $this->createdAt = new \DateTimeImmutable();
    }

    #[ORM\PreUpdate]
    public function onPreUpdate(): void { /* ... */ }

    public function getId(): ?int { return $this->id; }
}

enum ProductStatus: string
{
    case Draft = 'draft';
    case Published = 'published';
}
```

### Inferencia de tipos
Si la propiedad está tipada, Doctrine infiere el tipo de columna (`int` → `integer`, `string` → `string`, `bool` → `boolean`, `\DateTimeImmutable` → `datetime_immutable`, enums backed → su valor) y `nullable` a partir de `?`.

### Tipos más usados (`Doctrine\DBAL\Types\Types`)

| Constante | PHP | Notas |
|---|---|---|
| `INTEGER`, `SMALLINT`, `BIGINT` | `int` (`BIGINT` → `int`/`string` según DBAL) | En DBAL 4, `bigint` devuelve `int` cuando entra en el rango de PHP. |
| `STRING`, `TEXT`, `ASCII_STRING` | `string` | |
| `DECIMAL` | `string` | Evita errores de punto flotante. |
| `FLOAT`, `SMALLFLOAT` | `float` | `SMALLFLOAT` desde DBAL 4.1. |
| `BOOLEAN` | `bool` | |
| `DATE_IMMUTABLE`, `DATETIME_IMMUTABLE`, `DATETIMETZ_IMMUTABLE`, `TIME_IMMUTABLE` | `DateTimeImmutable` | Preferir las inmutables. |
| `DATEINTERVAL` | `DateInterval` | |
| `JSON` | `array`/mixed | `JSONB` en PostgreSQL vía `options: ['jsonb' => true]`. |
| `GUID` | `string` | |
| `BINARY`, `BLOB` | `string`/resource | |
| `SIMPLE_ARRAY` | `array` | Lista separada por comas. |
| `ENUM` | `string` | Tipo `ENUM` nativo de MySQL/MariaDB (DBAL 4.2+). |

> Los tipos `array` y `object` (serialización PHP) se eliminaron en DBAL 4. Usá `json` o un tipo propio.

### Identificadores

```php
// Autoincremental / secuencia según la plataforma
#[ORM\Id, ORM\GeneratedValue, ORM\Column]
private ?int $id = null;

// Estrategia explícita
#[ORM\GeneratedValue(strategy: 'SEQUENCE')]
#[ORM\SequenceGenerator(sequenceName: 'product_seq', allocationSize: 1)]

// UUID generado en PHP (p. ej. con symfony/uid)
#[ORM\Id, ORM\Column(type: 'uuid', unique: true)]
private Uuid $id;
// y en el constructor: $this->id = Uuid::v7();

// Clave compuesta
#[ORM\Id, ORM\Column] private int $orderId;
#[ORM\Id, ORM\Column] private int $lineNumber;
```

Estrategias: `AUTO` (por defecto), `IDENTITY`, `SEQUENCE`, `CUSTOM` (con `#[CustomIdGenerator]`), `NONE` (lo asignás vos).

> En ORM 3 + DBAL 4, `AUTO` en PostgreSQL pasa a usar `IDENTITY` en lugar de `SEQUENCE`. Se puede ajustar con `$config->setIdentityGenerationPreferences([...])`.

### Columnas especiales

```php
// Columna calculada por la DB (no se escribe desde PHP)
#[ORM\Column(insertable: false, updatable: false, generated: 'ALWAYS')]
private string $fullName;

// Valor por defecto a nivel DB (en 3.6 se deprecaron las expresiones como string;
// usar los objetos DefaultExpression de DBAL 4.4)
#[ORM\Column(options: ['default' => 0])]
private int $stock = 0;

// Propiedad readonly
#[ORM\Column]
private readonly string $sku;
```

### Embeddables (Value Objects)

```php
#[ORM\Embeddable]
class Address
{
    public function __construct(
        #[ORM\Column] public readonly string $street,
        #[ORM\Column] public readonly string $city,
        #[ORM\Column(length: 10)] public readonly string $zip,
    ) {}
}

#[ORM\Entity]
class Customer
{
    #[ORM\Embedded(class: Address::class, columnPrefix: 'billing_')]
    private Address $billingAddress;
}
```

Genera `billing_street`, `billing_city`, `billing_zip` en la tabla `customer`.

### Tipos personalizados

```php
use Doctrine\DBAL\Platforms\AbstractPlatform;
use Doctrine\DBAL\Types\Type;

final class MoneyType extends Type
{
    public function getSQLDeclaration(array $column, AbstractPlatform $platform): string
    {
        return $platform->getBigIntTypeDeclarationSQL($column);
    }

    public function convertToPHPValue($value, AbstractPlatform $platform): ?Money
    {
        return $value === null ? null : Money::ofMinor((int) $value);
    }

    public function convertToDatabaseValue($value, AbstractPlatform $platform): ?int
    {
        return $value?->minorAmount();
    }
}

Type::addType('money', MoneyType::class);
```

> En DBAL 4 se quitaron `getName()` y `requiresSQLCommentHint()`: ya no hacen falta los comentarios `(DC2Type:...)` en las columnas.

---

## 5. Relaciones (asociaciones)

### Conceptos clave
- **Lado propietario (owning side):** el que tiene la clave foránea. **Solo los cambios en este lado se persisten.**
- **Lado inverso (inverse side):** declara `mappedBy`; es solo para navegar.
- En `ManyToOne` / `OneToMany`, el propietario siempre es el `ManyToOne`.
- Siempre mantené **ambos lados sincronizados** en memoria.

### ManyToOne / OneToMany (la más común)

```php
#[ORM\Entity]
class Order
{
    #[ORM\ManyToOne(targetEntity: Customer::class, inversedBy: 'orders')]
    #[ORM\JoinColumn(nullable: false, onDelete: 'CASCADE')]
    private Customer $customer;

    /** @var Collection<int, OrderLine> */
    #[ORM\OneToMany(targetEntity: OrderLine::class, mappedBy: 'order',
        cascade: ['persist', 'remove'], orphanRemoval: true)]
    #[ORM\OrderBy(['position' => 'ASC'])]
    private Collection $lines;

    public function __construct(Customer $customer)
    {
        $this->customer = $customer;
        $this->lines = new ArrayCollection(); // ¡siempre inicializar!
    }

    public function addLine(OrderLine $line): void
    {
        if (!$this->lines->contains($line)) {
            $this->lines->add($line);
            $line->setOrder($this); // sincronizar el lado propietario
        }
    }

    public function removeLine(OrderLine $line): void
    {
        $this->lines->removeElement($line); // con orphanRemoval se borra
    }
}
```

> En ORM 3, `OneToMany` **requiere** `mappedBy`. Un OneToMany unidireccional se modela con `ManyToMany` + `JoinTable` con columna única.

### ManyToMany

```php
/** @var Collection<int, Tag> */
#[ORM\ManyToMany(targetEntity: Tag::class, inversedBy: 'products')]
#[ORM\JoinTable(name: 'product_tag')]
private Collection $tags;
```

Si la tabla intermedia necesita columnas extra (fecha, cantidad), convertila en una **entidad** propia con dos `ManyToOne`.

### OneToOne

```php
#[ORM\OneToOne(targetEntity: Profile::class, cascade: ['persist'])]
#[ORM\JoinColumn(nullable: true)]
private ?Profile $profile = null;
```

> El lado inverso de un OneToOne no puede ser lazy (Doctrine necesita consultar para saber si existe), lo que provoca consultas extra. Preferí relaciones unidireccionales desde el lado propietario.

### Opciones importantes

| Opción | Efecto |
|---|---|
| `cascade: ['persist']` | Al persistir el padre, persiste los hijos nuevos. |
| `cascade: ['remove']` | Borra los hijos vía ORM (los carga en memoria). |
| `onDelete: 'CASCADE'` (JoinColumn) | Borrado en cascada a nivel **base de datos** (más eficiente). |
| `orphanRemoval: true` | Borra el hijo al quitarlo de la colección. |
| `fetch: 'LAZY'` | Por defecto. Carga al acceder. |
| `fetch: 'EAGER'` | Carga siempre junto con el padre. |
| `fetch: 'EXTRA_LAZY'` | `count()`, `contains()`, `slice()` sin cargar toda la colección. |
| `indexBy: 'campo'` | La colección se indexa por ese campo. |

### Criteria sobre colecciones

```php
use Doctrine\Common\Collections\Criteria;
use Doctrine\Common\Collections\Order;

$criteria = Criteria::create()
    ->where(Criteria::expr()->eq('status', 'active'))
    ->orderBy(['createdAt' => Order::Descending])
    ->setMaxResults(5);

$recientes = $customer->getOrders()->matching($criteria);
// Si la colección no está inicializada, se traduce a SQL.
```

---

## 6. Herencia

### MappedSuperclass
Clase base **no persistible** que comparte campos (p. ej. timestamps).

```php
#[ORM\MappedSuperclass]
abstract class TimestampedEntity
{
    #[ORM\Column] protected \DateTimeImmutable $createdAt;
}
```

### Single Table Inheritance (STI)
Todas las subclases en una tabla, con columna discriminadora. Rápida, pero con columnas nullable.

```php
#[ORM\Entity]
#[ORM\InheritanceType('SINGLE_TABLE')]
#[ORM\DiscriminatorColumn(name: 'type', type: 'string')]
#[ORM\DiscriminatorMap(['person' => Person::class, 'company' => Company::class])]
abstract class Party { /* ... */ }
```

### Class Table Inheritance (JOINED)
Una tabla por clase, unidas por la PK. Esquema normalizado, pero con JOINs en cada consulta.

```php
#[ORM\InheritanceType('JOINED')]
```

> Desde ORM 3.6 la columna discriminadora puede usar un **enum** (`enumType`) y los valores del mapa se validan contra él.
> **Composición > herencia:** usala solo si realmente necesitás consultas polimórficas.

---

## 7. Repositorios

```php
$repo = $em->getRepository(Product::class);

$repo->find(1);
$repo->findAll();
$repo->findBy(['status' => ProductStatus::Published], ['name' => 'ASC'], limit: 10, offset: 0);
$repo->findOneBy(['sku' => 'ABC-1']);
$repo->count(['status' => ProductStatus::Draft]);
```

> Los métodos mágicos `findByName()` / `findOneBySku()` se eliminaron en ORM 3. Usá `findBy([...])` o métodos propios.

### Repositorio personalizado

```php
use Doctrine\ORM\EntityRepository;

/** @extends EntityRepository<Product> */
class ProductRepository extends EntityRepository
{
    /** @return list<Product> */
    public function findPublishedCheaperThan(string $max): array
    {
        return $this->createQueryBuilder('p')
            ->andWhere('p.status = :status')
            ->andWhere('p.price < :max')
            ->setParameter('status', ProductStatus::Published)
            ->setParameter('max', $max)
            ->orderBy('p.price', 'ASC')
            ->getQuery()
            ->getResult();
    }
}
```

En Symfony se suele extender `ServiceEntityRepository` para que el repositorio sea un servicio inyectable.

---

## 8. Consultas: DQL, QueryBuilder y SQL nativo

### DQL (Doctrine Query Language)
Parecido a SQL, pero sobre **clases y propiedades**, no tablas y columnas.

```php
$query = $em->createQuery(
    'SELECT o, c
     FROM App\Entity\Order o
     JOIN o.customer c
     WHERE o.total > :min AND c.country = :country
     ORDER BY o.createdAt DESC'
);
$query->setParameters(['min' => 100, 'country' => 'AR']);
$query->setMaxResults(20);
$orders = $query->getResult();
```

Funciones y cláusulas disponibles: `COUNT`, `SUM`, `AVG`, `MIN`, `MAX`, `CONCAT`, `SUBSTRING`, `LOWER`, `UPPER`, `LENGTH`, `TRIM`, `ABS`, `MOD`, `DATE_ADD`, `DATE_SUB`, `DATE_DIFF`, `CURRENT_DATE`, `COALESCE`, `NULLIF`, `CASE WHEN`, `IN`, `NOT IN`, `LIKE`, `BETWEEN`, `IS NULL`, `MEMBER OF`, `INSTANCE OF`, `SIZE()`, `EXISTS`, subconsultas, `GROUP BY`, `HAVING`.

Joins arbitrarios (entidades sin relación mapeada):

```sql
SELECT u, a FROM App\Entity\User u
LEFT JOIN App\Entity\AuditLog a ON a.userId = u.id
```

> Desde 3.6 los joins arbitrarios usan `ON` (antes `WITH`).

UPDATE / DELETE masivos (no pasan por el UnitOfWork ni disparan eventos):

```php
$em->createQuery('UPDATE App\Entity\Product p SET p.status = :s WHERE p.stock = 0')
   ->setParameter('s', ProductStatus::Draft)
   ->execute();
```

### QueryBuilder

```php
$qb = $em->createQueryBuilder();
$qb->select('p', 'c')
   ->from(Product::class, 'p')
   ->leftJoin('p.category', 'c')
   ->where($qb->expr()->orX(
       $qb->expr()->like('p.name', ':term'),
       $qb->expr()->eq('p.sku', ':sku'),
   ))
   ->andWhere('p.price BETWEEN :min AND :max')
   ->setParameter('term', '%silla%')
   ->setParameter('sku', 'ABC-1')
   ->setParameter('min', 10)
   ->setParameter('max', 100)
   ->orderBy('p.name')
   ->setFirstResult(0)
   ->setMaxResults(20);

$productos = $qb->getQuery()->getResult();
```

> **Siempre** usá parámetros (`:nombre`), nunca concatenes valores: evita inyección SQL.
> Desde 3.6 se pueden pasar *hints* directo al QueryBuilder.

### Métodos para obtener resultados

| Método | Devuelve |
|---|---|
| `getResult()` | Lista de objetos. |
| `getOneOrNullResult()` | Un objeto o `null` (excepción si hay más de uno). |
| `getSingleResult()` | Exactamente uno (excepción si no hay o hay varios). |
| `getSingleScalarResult()` | Un valor escalar (p. ej. un `COUNT`). |
| `getArrayResult()` | Arrays en lugar de objetos (más rápido, solo lectura). |
| `getScalarResult()` / `getSingleColumnResult()` | Valores planos. |
| `toIterable()` | Generador para recorrer grandes volúmenes. |

### SQL nativo

```php
use Doctrine\ORM\Query\ResultSetMappingBuilder;

$rsm = new ResultSetMappingBuilder($em);
$rsm->addRootEntityFromClassMetadata(Product::class, 'p');

$sql = 'SELECT ' . $rsm->generateSelectClause() . ' FROM products p WHERE MATCH(p.name) AGAINST (:q)';
$productos = $em->createNativeQuery($sql, $rsm)->setParameter('q', 'silla')->getResult();
```

O directamente con DBAL si no necesitás entidades:

```php
$rows = $em->getConnection()->fetchAllAssociative('SELECT id, name FROM products WHERE stock > ?', [0]);
```

### Paginación

```php
use Doctrine\ORM\Tools\Pagination\Paginator;

$query = $qb->getQuery()->setFirstResult(($page - 1) * 20)->setMaxResults(20);
$paginator = new Paginator($query, fetchJoinCollection: true);

$total = count($paginator);
foreach ($paginator as $product) { /* ... */ }
```

> `setMaxResults()` con un fetch join sobre una colección limita **filas**, no entidades. Por eso hace falta `Paginator`.

### Filtros SQL globales
Útiles para soft-delete o multi-tenant.

```php
use Doctrine\ORM\Mapping\ClassMetadata;
use Doctrine\ORM\Query\Filter\SQLFilter;

class TenantFilter extends SQLFilter
{
    public function addFilterConstraint(ClassMetadata $meta, string $alias): string
    {
        if (!$meta->hasField('tenantId')) return '';
        return sprintf('%s.tenant_id = %s', $alias, $this->getParameter('tenant'));
    }
}

$config->addFilter('tenant', TenantFilter::class);
$em->getFilters()->enable('tenant')->setParameter('tenant', 42);
```

---

## 9. Hidratación y DTOs

Para listados y reportes, no hace falta hidratar entidades completas. Usá **DTOs** con `NEW`:

```php
final class ProductListItem
{
    public function __construct(
        public readonly int $id,
        public readonly string $name,
        public readonly string $categoryName,
    ) {}
}

$items = $em->createQuery(
    'SELECT NEW App\Dto\ProductListItem(p.id, p.name, c.name)
     FROM App\Entity\Product p JOIN p.category c'
)->getResult();
```

En las versiones recientes de ORM 3 se agregaron mejoras a `NEW`, como DTOs anidados y argumentos con nombre.

Modos de hidratación (`$query->getResult(mode)`):
`HYDRATE_OBJECT` (default), `HYDRATE_ARRAY`, `HYDRATE_SCALAR`, `HYDRATE_SINGLE_SCALAR`, `HYDRATE_SCALAR_COLUMN`, `HYDRATE_SIMPLEOBJECT`.

Entidades de solo lectura:

```php
#[ORM\Entity(readOnly: true)]   // nunca se actualizan
// o por consulta:
$query->setHint(Query::HINT_READ_ONLY, true);
```

---

## 10. Performance

### El problema N+1
```php
// MAL: 1 consulta para órdenes + 1 por cada cliente
foreach ($orderRepo->findAll() as $order) {
    echo $order->getCustomer()->getName();
}
```

Soluciones:
1. **Fetch join:** `SELECT o, c FROM Order o JOIN o.customer c`.
2. **`fetch: 'EAGER'`** en la relación (solo si siempre la necesitás).
3. **Hint `fetchMode` por consulta:** `$query->setFetchMode(Order::class, 'customer', ClassMetadata::FETCH_EAGER)` (carga en lote con `IN`).
4. **DTOs** con `NEW` o arrays.

### Procesamiento por lotes

```php
$batchSize = 100;
foreach ($datos as $i => $fila) {
    $em->persist(new Product(...$fila));
    if (($i + 1) % $batchSize === 0) {
        $em->flush();
        $em->clear(); // libera memoria del UnitOfWork
    }
}
$em->flush();
$em->clear();
```

Para leer muchas filas:

```php
foreach ($em->createQuery('SELECT p FROM App\Entity\Product p')->toIterable() as $p) {
    // procesar...
    $em->detach($p);
}
```

### Caché

| Tipo | Qué guarda | Config |
|---|---|---|
| **Metadata cache** | El mapeo parseado. **Obligatorio en producción.** | `$config->setMetadataCache($pool)` |
| **Query cache** | DQL → SQL. **Obligatorio en producción.** | `$config->setQueryCache($pool)` |
| **Result cache** | Resultados de consultas. | `$query->enableResultCache(3600, 'clave')` |
| **Second Level Cache** | Entidades y colecciones entre requests. | `#[ORM\Cache(usage: 'READ_ONLY')]` |

Se usan pools PSR-6 (p. ej. `symfony/cache`). `doctrine/cache` ya no se usa en ORM 3.

### Otros consejos
- Generá los proxies de antemano en producción (`orm:generate-proxies`) o, con PHP 8.4+, usá lazy objects nativos.
- Usá `EXTRA_LAZY` en colecciones grandes.
- Creá índices para lo que filtrás y ordenás (`#[ORM\Index]`).
- Revisá las consultas con el profiler de Symfony o un middleware de logging de DBAL.

---

## 11. Eventos y listeners

### Lifecycle callbacks (en la entidad)

```php
#[ORM\HasLifecycleCallbacks]
class Post
{
    #[ORM\PrePersist]
    public function setCreatedAt(): void { $this->createdAt = new \DateTimeImmutable(); }

    #[ORM\PreUpdate]
    public function setUpdatedAt(): void { $this->updatedAt = new \DateTimeImmutable(); }
}
```

### Eventos disponibles

| Evento | Cuándo |
|---|---|
| `prePersist` / `postPersist` | Antes/después de persistir (post = después del INSERT). |
| `preUpdate` / `postUpdate` | Antes/después del UPDATE. En `preUpdate` se recibe el changeset. |
| `preRemove` / `postRemove` | Antes/después del borrado. |
| `postLoad` | Al hidratar una entidad. |
| `preFlush` / `onFlush` / `postFlush` | Alrededor del flush. `onFlush` permite inspeccionar todo el UnitOfWork. |
| `loadClassMetadata` | Al cargar metadata (para modificar mapeo dinámicamente). |
| `onClear` | Al llamar `clear()`. |

### Entity listener (clase separada)

```php
#[ORM\Entity]
#[ORM\EntityListeners([UserListener::class])]
class User { }

class UserListener
{
    public function preUpdate(User $user, PreUpdateEventArgs $args): void
    {
        if ($args->hasChangedField('email')) {
            $user->markEmailUnverified();
        }
    }
}
```

> En `preUpdate` no se pueden modificar relaciones ni llamar a `flush()`. Para cambios complejos usá `onFlush` y `recomputeSingleEntityChangeSet()`.
> Desde 3.6 hay comandos de consola para inspeccionar los listeners configurados.

---

## 12. Transacciones y concurrencia

### Transacciones

```php
// Implícita: flush() ya usa una transacción.

// Explícita y recomendada:
$em->wrapInTransaction(function (EntityManagerInterface $em) use ($from, $to, $amount) {
    $from->withdraw($amount);
    $to->deposit($amount);
    // flush automático al final; rollback si hay excepción
});

// Manual:
$em->getConnection()->beginTransaction();
try {
    // ...
    $em->flush();
    $em->getConnection()->commit();
} catch (\Throwable $e) {
    $em->getConnection()->rollBack();
    throw $e;
}
```

> Después de una excepción durante el `flush()`, el EntityManager queda **cerrado** y no se puede reutilizar. En Symfony se resetea con `ManagerRegistry::resetManager()`.

### Locking optimista
Con una columna `#[ORM\Version]`. Si dos procesos modifican la misma fila, el segundo recibe `OptimisticLockException`.

```php
$product = $em->find(Product::class, $id, LockMode::OPTIMISTIC, $expectedVersion);
```

### Locking pesimista (a nivel DB)

```php
$em->wrapInTransaction(function ($em) use ($id) {
    $account = $em->find(Account::class, $id, LockMode::PESSIMISTIC_WRITE); // SELECT ... FOR UPDATE
    $account->debit(100);
});
```

---

## 13. Doctrine DBAL

Útil sola (sin ORM) o para consultas de alto rendimiento.

```php
$conn = DriverManager::getConnection($params);

// Lectura
$users = $conn->fetchAllAssociative('SELECT * FROM users WHERE active = ?', [1]);
$user  = $conn->fetchAssociative('SELECT * FROM users WHERE id = :id', ['id' => 5]);
$count = $conn->fetchOne('SELECT COUNT(*) FROM users');
$ids   = $conn->fetchFirstColumn('SELECT id FROM users');

// Escritura
$conn->insert('users', ['email' => 'a@b.com', 'active' => true]);
$conn->update('users', ['active' => false], ['id' => 5]);
$conn->delete('users', ['id' => 5]);
$afectadas = $conn->executeStatement('UPDATE users SET active = 0 WHERE last_login < ?', ['2025-01-01']);

// Listas en IN
use Doctrine\DBAL\ArrayParameterType;
$conn->fetchAllAssociative('SELECT * FROM users WHERE id IN (?)', [[1, 2, 3]], [ArrayParameterType::INTEGER]);

// Iteración eficiente
foreach ($conn->iterateAssociative('SELECT * FROM big_table') as $row) { /* ... */ }

// Transacción
$conn->transactional(function (Connection $conn) { /* ... */ });
```

### QueryBuilder de DBAL

```php
$qb = $conn->createQueryBuilder()
    ->select('u.id', 'u.email')
    ->from('users', 'u')
    ->where('u.active = :active')
    ->setParameter('active', true)
    ->orderBy('u.email')
    ->setMaxResults(10);

$rows = $qb->executeQuery()->fetchAllAssociative();
```

DBAL 4.1 agregó `UNION` al QueryBuilder y DBAL 4.4 una forma más cómoda de crear builders para subconsultas.

### Schema Manager

```php
$sm = $conn->createSchemaManager();
$sm->listTableNames();
$table = $sm->introspectTable('users');
$sm->tableExists('users');
```

### Middlewares
Reemplazan al viejo `SQLLogger`. Permiten envolver drivers, conexiones y statements (logging, métricas, reintentos).

```php
use Doctrine\DBAL\Logging\Middleware as LoggingMiddleware;
$config->setMiddlewares([new LoggingMiddleware($psrLogger)]);
```

---

## 14. Doctrine Migrations

```bash
vendor/bin/doctrine-migrations generate          # migración vacía
vendor/bin/doctrine-migrations diff              # genera según diferencias mapeo ↔ DB
vendor/bin/doctrine-migrations migrate           # aplica pendientes
vendor/bin/doctrine-migrations migrate prev      # vuelve una atrás
vendor/bin/doctrine-migrations status
vendor/bin/doctrine-migrations list
vendor/bin/doctrine-migrations execute 'DoctrineMigrations\Version20261028120000' --down
vendor/bin/doctrine-migrations sync-metadata-storage
```

En Symfony: `php bin/console make:migration` y `php bin/console doctrine:migrations:migrate`.

```php
final class Version20261028120000 extends AbstractMigration
{
    public function getDescription(): string { return 'Agrega columna stock'; }

    public function up(Schema $schema): void
    {
        $this->addSql('ALTER TABLE products ADD stock INT NOT NULL DEFAULT 0');
    }

    public function down(Schema $schema): void
    {
        $this->addSql('ALTER TABLE products DROP stock');
    }

    public function isTransactional(): bool
    {
        return false; // MySQL hace commit implícito en DDL
    }
}
```

Buenas prácticas:
- **Nunca** uses `orm:schema-tool:update --force` en producción: usá migraciones.
- Revisá el SQL generado por `diff` antes de commitearlo.
- Separá cambios de esquema y migraciones de datos grandes.
- Escribí `down()` cuando sea realista hacer rollback.

---

## 15. Otros paquetes del ecosistema

- **DoctrineBundle** (Symfony): integración, configuración YAML, comandos `doctrine:*`, profiler.
- **DoctrineFixturesBundle / data-fixtures:** datos de prueba (`doctrine:fixtures:load`).
- **Foundry (zenstruck/foundry):** factories para tests.
- **Gedmo DoctrineExtensions / StofDoctrineExtensionsBundle:** Timestampable, Sluggable, SoftDeleteable, Tree, Translatable, Loggable.
- **ramsey/uuid-doctrine** o **symfony/uid**: tipos UUID/ULID.
- **scienta/doctrine-json-functions:** funciones JSON en DQL.
- **beberlei/doctrineextensions:** funciones DQL extra por plataforma (`DATE_FORMAT`, `YEAR`, `RAND`, etc.).
- **phpstan-doctrine:** análisis estático de DQL, QueryBuilder y mapeo.

---

## 16. Buenas prácticas y errores comunes

**Modelado**
- Inicializá **siempre** las colecciones en el constructor con `new ArrayCollection()`.
- Tipá las colecciones como `Collection` (interfaz), no `ArrayCollection`.
- Exponé métodos de dominio (`addLine`, `publish()`) en lugar de setters para todo.
- Usá `DateTimeImmutable` y tipos inmutables.
- Usá Value Objects con `Embeddable`.
- Preferí asociaciones unidireccionales cuando el lado inverso no hace falta.

**Persistencia**
- `persist()` solo hace falta con entidades **nuevas**; las gestionadas se actualizan solas con `flush()`.
- Hacé `flush()` una vez por caso de uso, no dentro de loops ni en cada setter.
- No hagas `flush()` dentro de listeners de `preUpdate`/`postPersist`.
- Usá `getReference()` para asignar relaciones cuando solo tenés el ID.

**Consultas**
- Vigilá el N+1 (activá el profiler o log de SQL en desarrollo).
- Usá DTOs/arrays para listados y reportes.
- Paginá con `Paginator` cuando hay fetch joins sobre colecciones.
- Parámetros siempre: nunca concatenes input del usuario en DQL/SQL.

**Errores típicos**

| Error | Causa habitual |
|---|---|
| `A new entity was found through the relationship ... that was not configured to cascade persist` | Asignaste una entidad nueva a una relación y no la persististe (falta `persist()` o `cascade: ['persist']`). |
| `The EntityManager is closed` | Hubo una excepción en un `flush()` anterior. |
| `Typed property ... must not be accessed before initialization` | Propiedad no nullable sin valor inicial (p. ej. colección no inicializada). |
| Cambios que no se guardan en una relación | Modificaste solo el lado inverso. |
| Memoria creciente en scripts largos | No llamás a `clear()` en procesos batch. |
| `Binding entities to query parameters only allowed for entities that have an identifier` | Pasaste una entidad no persistida como parámetro. |

---

## 17. Cambios por versión

### Requisitos de PHP (resumen)

| Paquete / versión | PHP mínimo |
|---|---|
| ORM 2.7 – 2.8 | 7.1 – 7.2 |
| ORM 2.10 – 2.20 | 7.1+ (atributos requieren 8.0) |
| ORM 3.x | 8.1 |
| DBAL 3.x | 7.4 |
| DBAL 4.x | 8.1 |
| Migrations 3.x | 7.2 → 8.1 en las últimas minor |

### Doctrine ORM 2.x (hitos principales)

| Versión | Año aprox. | Cambios destacados |
|---|---|---|
| **2.5** | 2015 | Second Level Cache, Embeddables, entity listeners mejorados. |
| **2.6** | 2017 | PHP 7.1, mejoras de rendimiento y correcciones en el cálculo del orden de commits. |
| **2.7** | 2020 | Inicio de deprecaciones pensando en 3.0 (`merge`, `detach` en cascada, `flush($entity)`). Soporte de `doctrine/persistence` 2. |
| **2.8** | 2020 | `Query::toIterable()` (reemplazo de `iterate()`), PHP 8. |
| **2.9** | 2021 | **Mapeo con atributos PHP 8**. Inferencia de tipo de columna desde propiedades tipadas. Soporte inicial de DBAL 3. |
| **2.10** | 2021 | `wrapInTransaction()` (reemplaza `transactional()`), soporte completo de DBAL 3. |
| **2.11** | 2022 | **Enums de PHP 8.1** (`enumType`), propiedades `readonly`, atributos anidados (`JoinTable` con `JoinColumn`). |
| **2.12** | 2022 | `ORMSetup` (reemplaza `Tools\Setup`), deprecación de `doctrine/cache` en favor de PSR-6. |
| **2.13** | 2022 | Columnas `insertable`/`updatable`/`generated`. Deprecación de anotaciones a favor de atributos/XML. |
| **2.14** | 2023 | Deprecación de `EntityManager::create()`, de partial objects y de varios métodos mágicos. Proxies basados en lazy ghost de `symfony/var-exporter` (opcional). |
| **2.15 – 2.19** | 2023-2024 | Capa de compatibilidad hacia 3.0: deprecaciones del driver YAML y de anotaciones, `OneToMany` sin `mappedBy`, métodos mágicos `findBy*`, `Paginator` y arrays de mapeo; soporte de DBAL 4. |
| **2.20** | 2024 → hoy | Última rama 2.x, todavía con releases de mantenimiento en 2026. Ideal como paso previo a migrar a 3. |

### Doctrine ORM 3.0 (febrero 2024) — versión mayor

**Eliminado:**
- Mapeo por **anotaciones** (docblocks) y por **YAML** (solo quedan atributos y XML; también se puede usar PHP puro).
- `EntityManager::merge()` y el cascade `merge`.
- `EntityManager::flush($entity)` con argumento y `clear($entityName)` parcial.
- `EntityManager::create()` → usar el constructor.
- `Query::iterate()` → usar `toIterable()`.
- Métodos mágicos de repositorio (`findByX`, `findOneByX`, `countByX`).
- Partial objects (`PARTIAL` en DQL) en 3.0; luego se reintrodujo de forma acotada en versiones 3.x posteriores.
- `OneToMany` unidireccional sin `mappedBy`.
- Dependencia de `doctrine/cache` (solo PSR-6).
- Comandos y helpers obsoletos (`orm:convert-mapping`, `orm:generate-entities`, `orm:convert-d1-schema`, generador de repositorios, etc.).
- Generación de proxies con el generador propio legado → lazy ghosts de `symfony/var-exporter`.

**Cambiado:**
- Requiere **PHP 8.1** y DBAL 3.8+/4.
- Los mapeos internos pasan de arrays a **objetos tipados** (`FieldMapping`, `ManyToOneAssociationMapping`, etc.).
- `AUTO` en PostgreSQL usa `IDENTITY` con DBAL 4 (configurable).
- Tipado estricto en firmas de la API pública.

### Doctrine ORM 3.1 – 3.6

| Versión | Cambios destacados |
|---|---|
| **3.1 – 3.3** | Correcciones de la migración a 3.0, mejoras de tipado y rendimiento; vuelta acotada de funcionalidades muy pedidas que se habían quitado (como `PARTIAL`) y mejoras en DQL. |
| **3.4** | Soporte de **lazy objects nativos de PHP 8.4** (`$config->enableNativeLazyObjects(true)`), mejoras en `NEW` (DTOs anidados, argumentos con nombre). |
| **3.5** | Más deprecaciones preparando 4.0 (la vía recomendada pasa a ser lazy objects nativos en PHP 8.4+), deprecación de expresiones `default` como string en puntos de la API. |
| **3.6** (dic. 2025) | *Hints* en QueryBuilder; comandos para inspeccionar listeners; salida JSON en `orm:mapping:describe`; `DiscriminatorColumn` con `enumType`; soporte de `ClassLocator`; joins arbitrarios con `ON` en vez de `WITH`; soporte de Symfony 8; se deprecan expresiones `default` como string, `FieldMapping::$default` y `nullable` en columnas PK. |

> Para el detalle exacto de cada minor, conviene leer `UPGRADE.md` y las release notes del repositorio `doctrine/orm`: algunas features listadas para 3.1–3.5 se agrupan aproximadamente.

**Mirando hacia ORM 4:** se espera que elimine lo deprecado en 3.x (proxies no nativos, expresiones default como string, etc.) y exija una versión de PHP más nueva. Activar los avisos de deprecación (`doctrine/deprecations`) es la mejor forma de prepararse.

### Doctrine DBAL

| Versión | Cambios destacados |
|---|---|
| **2.x** | API basada en `Statement` con `fetch()`/`fetchAll()` y modos PDO; `SQLLogger`. |
| **3.0** (2020) | Nueva API de resultados: `executeQuery()` devuelve `Result` con `fetchAssociative()`, `fetchAllAssociative()`, `fetchOne()`, `iterateAssociative()`. `executeStatement()` para escrituras. Separación de capas Driver/Connection. |
| **3.1 – 3.10** | Middlewares (reemplazan `SQLLogger`), `createSchemaManager()`, `introspectTable()`, soporte de nuevas versiones de plataformas, y deprecaciones hacia 4.0 (`query()`, `exec()`, fetch modes viejos, parámetro `url`, comentarios `DC2Type`). |
| **4.0** (feb. 2024) | Requiere PHP 8.1. `ParameterType` y `ArrayParameterType` como **enums**. Eliminados `Connection::query()`, `exec()`, `executeUpdate()`, el parámetro `url` (→ `DsnParser`), `SQLLogger`, los tipos `array`/`object`, `Type::getName()` y los comentarios `(DC2Type:...)`. `bigint` se hidrata como `int` cuando cabe. |
| **4.1** | `UNION` en QueryBuilder, tipo `SMALLFLOAT`, plataforma PostgreSQL 12+ por defecto, deprecación de algunas plataformas viejas de MySQL/MariaDB. |
| **4.2** | Tipo `ENUM` nativo para MySQL/MariaDB; uso de las subclases nuevas de PDO de PHP 8.4; soporte pasivo de tablas particionadas en PostgreSQL. |
| **4.3** | Mejoras y correcciones de schema management y plataformas. |
| **4.4** | `DefaultExpression` para valores por defecto, nueva API de introspección de esquema, editor de vistas, builder de subconsultas, deprecación de extender clases de schema y del parámetro de conexión `service`. |

### Doctrine Migrations

| Versión | Cambios destacados |
|---|---|
| **2.x** | Configuración por XML/YAML/PHP, tabla de versiones simple. |
| **3.0** (2020) | Rediseño: `DependencyFactory`, nueva configuración, *metadata storage* (tabla con fecha de ejecución y duración), comandos `sync-metadata-storage`, `list`, soporte de múltiples namespaces. |
| **3.1 – 3.7** | Soporte de DBAL 4 y ORM 3, `migrate` con `--all-or-nothing` mejorado, atributos en lugar de anotaciones en la documentación, mejoras en mensajes de salida y en `diff`. |

### Doctrine Annotations / Cache

- `doctrine/annotations`: deprecado. No lo uses en código nuevo.
- `doctrine/cache`: deprecado. Usá cualquier implementación PSR-6 (`symfony/cache`).

---

## 18. Checklist de migración ORM 2 → 3 y DBAL 3 → 4

1. Subí primero a **ORM 2.20** y **DBAL 3.10** (las últimas de cada rama).
2. Activá y corregí todas las **deprecaciones** (`doctrine/deprecations`, PHPUnit con `--display-deprecations`, logs de Symfony).
3. Convertí el mapeo de **anotaciones/YAML a atributos** (Rector tiene reglas para esto: `DoctrineSetList::ANNOTATIONS_TO_ATTRIBUTES`).
4. Reemplazá:
   - `EntityManager::create()` → `new EntityManager()`
   - `merge()` → recargar y aplicar cambios
   - `flush($entity)` → `flush()`
   - `clear(Clase::class)` → `clear()` o `detach()`
   - `iterate()` → `toIterable()`
   - `findByName()` → `findBy(['name' => ...])`
   - `transactional()` → `wrapInTransaction()`
5. Agregá `mappedBy` a todos los `OneToMany`.
6. Reemplazá `doctrine/cache` por cachés PSR-6.
7. En DBAL:
   - `query()`/`exec()`/`executeUpdate()` → `executeQuery()`/`executeStatement()`
   - `fetchAll()`/`fetch()` → `fetchAllAssociative()`/`fetchAssociative()`
   - Constantes `Connection::PARAM_*_ARRAY` → `ArrayParameterType::*`
   - `url` → `DsnParser`
   - `SQLLogger` → middleware de logging
   - Tipos `array`/`object` → `json` (requiere migración de datos)
   - En tipos custom, quitar `getName()` y `requiresSQLCommentHint()`
8. Si usás PostgreSQL con `AUTO`, revisá la estrategia de IDs (secuencias vs identity) antes de generar migraciones.
9. Generá un `migrations:diff` tras actualizar: los comentarios `DC2Type` desaparecen y conviene aplicar esa migración por separado.
10. Corré los tests, `orm:validate-schema` y PHPStan con `phpstan-doctrine`.
11. Si estás en PHP 8.4+, activá `enableNativeLazyObjects(true)`.

---

### Recursos

- Documentación oficial: https://www.doctrine-project.org/projects/orm.html
- `UPGRADE.md` de cada repositorio en https://github.com/doctrine
- Symfony + Doctrine: https://symfony.com/doc/current/doctrine.html

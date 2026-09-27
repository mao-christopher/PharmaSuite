using System;
using System.Collections.Generic;
using UnityEngine;

namespace Pharma.Simulation
{
    [Serializable] public class ShelfRegion
    {
        public string id, kind, medication;
        public Vector3 center, size;
        public float yaw;
    }

    [Serializable] public class BottlePlacement
    {
        public string id, home;
        public Vector3 origin;
        public float binOffsetX;
    }

    [Serializable] public class CollisionBox
    {
        public string name;
        public Matrix4x4 transform;
        public Vector3 size;
        public bool walkable;
    }

    /// <summary>Plain room data: regions, placement points, bottles, and navigation boxes.
    /// Captured from the hand-built scene today; a room data file can fill it instead.</summary>
    [Serializable] public class RoomDescription
    {
        public float floorY;
        public ShelfRegion[] regions;
        public Vector3 counterContact, disposalContact;
        public float binBottomY;
        public BottlePlacement[] bottles;
        public CollisionBox[] collisionBoxes;
        public Bounds navigationBounds;

        public ShelfRegion Region(string id) => Array.Find(regions, r => r.id == id);
        public BottlePlacement Bottle(string id) => Array.Find(bottles, b => b.id == id);

        /// <summary>Where the technician stands to reach a contact point in a region.</summary>
        public Vector3 StandFor(Vector3 contact, string region)
        {
            float zOffset = region == "counter" ? .63f : region == "disposal" ? .54f : .49f;
            return new Vector3(contact.x - .32f, floorY, contact.z - zOffset);
        }

        /// <summary>Navigation boxes from the scene's solid (PharmaSolid) and floor (PharmaWalkable) colliders.</summary>
        public static CollisionBox[] SceneBoxes()
        {
            var boxes = new List<CollisionBox>();
            foreach (var box in UnityEngine.Object.FindObjectsByType<BoxCollider>(FindObjectsSortMode.None))
            {
                if (!box.enabled || box.isTrigger || !box.gameObject.activeInHierarchy) continue;
                int layer = box.gameObject.layer;
                if (layer != CollisionWorld.SolidLayer && layer != CollisionWorld.FloorLayer) continue;
                boxes.Add(new CollisionBox { name = box.name,
                    transform = box.transform.localToWorldMatrix * Matrix4x4.Translate(box.center),
                    size = box.size, walkable = layer == CollisionWorld.FloorLayer
                });
            }
            return boxes.ToArray();
        }

        public static RoomDescription FromScene(ShelfRegion[] regions, Transform technician, Transform bottleOne, Transform bottleTwo)
        {
            return new RoomDescription {
                floorY = technician.position.y, regions = regions,
                counterContact = new Vector3(-2.45f, 1.177f, -1.08f),
                disposalContact = new Vector3(2.5f, 1.02f, -.75f), binBottomY = .21f,
                bottles = new[] {
                    new BottlePlacement { id = "bottle-a1", home = "shelf-a", origin = bottleOne.position, binOffsetX = -.065f },
                    new BottlePlacement { id = "bottle-a2", home = "shelf-a", origin = bottleTwo.position, binOffsetX = .065f } },
                collisionBoxes = SceneBoxes(),
                navigationBounds = new Bounds(new Vector3(0, 1, 2), new Vector3(10, 5, 12))
            };
        }

        /// <summary>A room built from the dashboard's rebuilt boxes (M7): regions and colliders only.</summary>
        public static RoomDescription Rebuilt(ShelfRegion[] regions, float floorY)
        {
            var boxes = SceneBoxes();
            var bounds = new Bounds(Vector3.zero, Vector3.zero);
            for (int i = 0; i < boxes.Length; i++)
            {
                var corners = new Vector3[8];
                for (int k = 0; k < 8; k++)
                    corners[k] = Vector3.Scale(boxes[i].size * .5f, new Vector3((k&1)==0?-1:1, (k&2)==0?-1:1, (k&4)==0?-1:1));
                var b = GeometryUtility.CalculateBounds(corners, boxes[i].transform);
                if (i == 0) bounds = b; else bounds.Encapsulate(b);
            }
            bounds.Expand(1);
            return new RoomDescription { floorY = floorY, regions = regions, bottles = new BottlePlacement[0],
                collisionBoxes = boxes, navigationBounds = bounds };
        }
    }
}

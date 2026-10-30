-- Router 2.0.0: compare only each layout's stable cells, allow a small slide,
-- and keep one threshold per layout. Distances are then stored as a fraction
-- of the nearest layout's threshold (<= 1 = recognised); layout_model.threshold
-- is 1.0 for these models. 1.0.0 rows keep their meaning and still load.
ALTER TABLE layout_model ADD COLUMN slide_cells INTEGER NOT NULL DEFAULT 0 CHECK (slide_cells BETWEEN 0 AND 4);
ALTER TABLE layout_centroid ADD COLUMN stable_mask BOOLEAN[];
ALTER TABLE layout_centroid ADD COLUMN threshold DOUBLE PRECISION;
